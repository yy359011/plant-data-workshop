<script setup lang="ts">
// 文献整理工作台（对照 ChapterWorkshop.jsx / BatchReviewBar.jsx）
// 保留 cw-simple 简化布局与 ph-batch-review 抽检条；弹窗沿用 cw-modal 样式实现 1:1 复刻。
import { computed, defineComponent, h, onMounted, onUnmounted, ref, watch } from 'vue';
import type { Component } from 'vue';
import {
  IconFolder,
  IconUpload,
  IconFileText,
  IconPhoto,
  IconListDetails,
  IconSettings,
  IconDatabase,
  IconBook2,
  IconShieldCheck,
  IconArrowRight,
  IconChevronLeft,
  IconChevronRight,
  IconPlus,
  IconX,
  IconDownload,
  IconExternalLink,
  IconFlask,
  IconCheck,
  IconAlertTriangle,
  IconLoader2,
  IconTable,
  IconTrash,
  IconLock,
  IconArrowLeft,
} from '@tabler/icons-vue';
import AppIcon from '../components/common/AppIcon.vue';
import PdfViewer from '../components/PdfViewer.vue';
import { platformRequest } from '../api/client';
import { go, setToast, workspace, routeOf, refreshWorkspace } from '../stores/workspace';
import { readHash } from '../composables/useHashRoute';
import { date as dateLabel } from '../utils/format';

const props = defineProps<{ user: any }>();

// ---------- 模块级常量与纯函数 ----------
const documentTitle = (value: unknown): string =>
  String(value || '未命名文献')
    .replace(/[（(](?:真实论文|章节管线合成示例|合成演示|测试文献)[）)]/g, '')
    .trim();

const statusNames: Record<string, string> = {
  pending: '待加工',
  ready: '已编排',
  queued: '排队中',
  processing: '加工中',
  completed: '待复核',
  review: '待复核',
  approved: '已通过',
  rejected: '不采用',
  error: '加工失败',
  materialized: '已送入下游',
};

const purposeNames: Record<string, string> = {
  both: '训练数据 + 知识库',
  training: '仅训练数据',
  kb: '仅知识库',
};

const licenseNames: Record<string, string> = {
  self_owned: '自有 / 已获授权',
  research_only: '仅内部知识库',
  unknown: '用途待核对',
};

const fieldTypeNames: Record<string, string> = { text: '文本', number: '数值', boolean: '是 / 否' };

const blank = { documents: [], jobs: [], model: { endpoint: '', model: '', vision: false, configured: false, has_key: false } };

function byteSize(n: number): string {
  return n >= 1048576 ? `${(n / 1048576).toFixed(1)} MiB` : `${Math.max(1, Math.ceil(n / 1024))} KiB`;
}

function encodeFile(file: File): Promise<string> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(String(reader.result).split(',')[1]);
    reader.onerror = () => reject(Error(`无法读取 ${file.name}`));
    reader.readAsDataURL(file);
  });
}

/** 文献加工接口：自动注入 hash 中的 batch_id，401 由 platformRequest 派发会话过期事件 */
function request(path: string, body?: unknown): Promise<any> {
  const batch = readHash().get('batch');
  const payload = body === undefined ? undefined : batch ? { ...(body as Record<string, unknown>), batch_id: batch } : body;
  return platformRequest('/api/chapters' + path, payload);
}

const interpretationOf = (result: any): { text: string; source_pages: number[] } =>
  typeof result?.interpretation === 'string'
    ? { text: result.interpretation, source_pages: [] }
    : { text: result?.interpretation?.text || '', source_pages: [...(result?.interpretation?.source_pages || [])] };

const draftValues = (editor: any) => ({
  corpus: editor.text || '',
  interpretation: editor.interpretation || { text: '', source_pages: [] },
  structured_fields: editor.fields || [],
});

const draftSignature = (editor: any): string => JSON.stringify(draftValues(editor));

function draftProblems(editor: any, schema: any[], pages: any[]): string[] {
  const issues: string[] = [];
  const pageNumbers = new Set(pages.map((page) => page.number));
  const definitions = new Map(schema.map((field) => [field.key, field]));
  if (editor.interpretation?.text.trim() && !editor.interpretation.source_pages?.length) issues.push('独立解读需要选择出处页。');
  for (const field of editor.fields || []) {
    if (field.status !== 'found') continue;
    const definition = definitions.get(field.key);
    const label = definition?.label || field.key;
    if (
      field.value == null ||
      field.value === '' ||
      (definition?.type === 'number' && (typeof field.value !== 'number' || !Number.isFinite(field.value))) ||
      (definition?.type === 'boolean' && typeof field.value !== 'boolean')
    )
      issues.push(`${label}：填写符合字段类型的值。`);
    if (!field.evidence?.trim()) issues.push(`${label}：请粘贴原文证据。`);
    if (!field.source_pages?.length || field.source_pages.some((number: number) => !pageNumbers.has(number)))
      issues.push(`${label}：请选择本章的来源页。`);
  }
  return issues;
}

function editorFrom(chapter: any) {
  const result = chapter?.result || {};
  const text = chapter?.corpus || result.corpus || '';
  const value = {
    chapterId: chapter?.id || '',
    revision: chapter?.revision ?? 0,
    text,
    baselineText: text,
    interpretation: interpretationOf(result),
    fields: JSON.parse(JSON.stringify(result.structured_fields || [])),
    conflict: false,
  };
  return { ...value, baseline: draftSignature(value) };
}

function resolveMarkdownImage(source: string, images: any[]): any {
  const raw = String(source || '').replace(/^<|>$/g, '');
  const byId = images.find((image) => raw === image.id || raw === `image:${image.id}` || raw === `image://${image.id}`);
  if (byId) return byId;
  if (/[\\\u0000-\u001f]/.test(raw)) return null;
  if (/^(?:https?:)?\/\//i.test(raw) || raw.startsWith('/')) {
    try {
      const url = new URL(raw, location.origin);
      if (url.origin !== location.origin || url.search || url.hash) return null;
      return images.find((image) => image.url === url.pathname) || null;
    } catch {
      return null;
    }
  }
  const path = raw.replace(/^\.\//, '');
  if (path.split('/').some((part) => part === '..') || /%2e|%2f|%5c/i.test(path)) return null;
  return images.find((image) => path === image.path?.replace(/^\.\//, '')) || null;
}

const plantFieldTemplate = [
  { key: 'scientific_name', label: '植物学名', type: 'text', description: '文中明确使用的拉丁学名，保留原文拼写。' },
  { key: 'family', label: '科', type: 'text', description: '文中明确提及的分类科名。' },
  { key: 'growth_habit', label: '生长型', type: 'text', description: '文献明确描述的草本、灌木、藤本等生长型。' },
  { key: 'leaf_shape', label: '叶形', type: 'text', description: '原文叶片形态描述，不根据照片推断。' },
  { key: 'plant_height_cm', label: '株高（cm）', type: 'number', description: '仅填写文中明确报告、能确定单位为cm的单个数值；范围保留在证据，不能取均值。' },
  { key: 'habitat', label: '生境', type: 'text', description: '文中记载的生境或分布环境。' },
  { key: 'flowering_period', label: '花期', type: 'text', description: '文中明确记载的开花时间。' },
  { key: 'is_endemic', label: '是否特有', type: 'boolean', description: '仅当文中明确说明是或不是特有种时填写，未提及为null。' },
];

// ---------- 子组件 ----------

/** 章节工作台弹窗外壳：复刻 cw-modal 结构与全局样式 */
const CwDialog = defineComponent({
  props: { title: { type: String, default: '' }, subtitle: { type: String, default: '' }, wide: { type: Boolean, default: false } },
  emits: ['close'],
  setup(props, { emit, slots }) {
    onMounted(() => {
      const previous = document.body.style.overflow;
      document.body.style.overflow = 'hidden';
      const handle = (event: KeyboardEvent) => {
        if (event.key === 'Escape') emit('close');
      };
      document.addEventListener('keydown', handle);
      onUnmounted(() => {
        document.body.style.overflow = previous;
        document.removeEventListener('keydown', handle);
      });
    });
    return () =>
      h('div', { class: 'cw-modal-backdrop', onClick: (e: MouseEvent) => { if (e.target === e.currentTarget) emit('close'); } }, [
        h('section', { class: ['cw-modal', props.wide ? 'wide' : ''], role: 'dialog', 'aria-modal': 'true', 'aria-label': props.title }, [
          h('header', { class: 'cw-modal-heading' }, [
            h('div', [h('h2', props.title), props.subtitle ? h('p', props.subtitle) : null]),
            h('button', { type: 'button', class: 'cw-icon-button', 'aria-label': '关闭', onClick: () => emit('close') }, [h(AppIcon, { icon: IconX, size: 18 })]),
          ]),
          slots.default?.(),
        ]),
      ]);
  },
});

/** 逐页 Markdown 渲染：支持图片/来源页链接/表格/标题/代码/列表/引用 */
const PageMarkdown = defineComponent({
  props: { text: { type: String, default: '' }, images: { type: Array, default: () => [] }, pdfs: { type: Array, default: () => [] } },
  emits: ['page-link', 'image'],
  setup(props, { emit }) {
    return () => {
      const lines = String(props.text || '本页没有可读取的文字。')
        .replace(/\r\n?/g, '\n')
        .split('\n');
      const blocks: any[] = [];

      const inline = (value: string): any[] | string => {
        const input = String(value || '');
        const pattern = /(!?)\[([^\]]*)\]\(([^\s)]+)(?:\s+"[^"]*")?\)|\*\*([^*]+)\*\*|`([^`]+)`/g;
        const output: any[] = [];
        let cursor = 0;
        let match: RegExpExecArray | null;
        while ((match = pattern.exec(input))) {
          if (match.index > cursor) output.push(input.slice(cursor, match.index));
          const key = `${match.index}-${cursor}`;
          if (match[4]) output.push(h('strong', { key }, match[4]));
          else if (match[5]) output.push(h('code', { key }, match[5]));
          else {
            const isImage = Boolean(match[1]);
            const label = match[2];
            const source = match[3];
            const image = resolveMarkdownImage(source, props.images as any[]);
            const pageMatch = source.match(/^#(?:page|source-page)=(\d+)$/);
            if (isImage) {
              output.push(
                image
                  ? h('span', { class: 'cw-md-image', key }, [
                      h('button', { type: 'button', onClick: () => emit('image', image), 'aria-label': `查看原始图片 ${label || image.caption || image.path}` }, [
                        h('img', { src: image.url, alt: label || image.caption || '原始文献图片', loading: 'lazy' }),
                      ]),
                      h('small', label || image.caption || image.path),
                    ])
                  : h('span', { class: 'cw-md-blocked', key }, [`[图片未匹配到本章原件：${label || source}]`]),
              );
            } else if (pageMatch) {
              output.push(h('button', { class: 'cw-source-link', type: 'button', key, onClick: () => emit('page-link', Number(pageMatch[1])) }, label || `第 ${pageMatch[1]} 页`));
            } else if (image) {
              output.push(h('button', { class: 'cw-source-link', type: 'button', key, onClick: () => emit('image', image) }, label || '查看原图'));
            } else {
              let href: string | null = null;
              try {
                const url = new URL(source, location.origin);
                if (/^https?:$/.test(url.protocol) && (url.origin !== location.origin || (props.pdfs as any[]).some((pdf) => pdf.url === url.pathname && !url.search && !url.hash))) href = url.href;
              } catch {
                /* 非法链接按普通文本处理 */
              }
              output.push(
                href
                  ? h('a', { key, href, target: '_blank', rel: 'noopener noreferrer' }, [label || source, h(AppIcon, { icon: IconExternalLink, size: 12 })])
                  : h('span', { class: 'cw-md-blocked-link', key }, label || source),
              );
            }
          }
          cursor = pattern.lastIndex;
        }
        if (cursor < input.length) output.push(input.slice(cursor));
        return output.length ? output : input;
      };

      const cells = (line: string): string[] =>
        line
          .trim()
          .replace(/^\|/, '')
          .replace(/\|$/, '')
          .split(/(?<!\\)\|/)
          .map((cell) => cell.trim().replace(/\\\|/g, '|'));
      const separator = (line?: string): boolean => !!line?.includes('|') && cells(line).every((cell) => /^:?-{3,}:?$/.test(cell));
      const tableStart = (index: number): boolean => lines[index]?.includes('|') && separator(lines[index + 1]);

      let index = 0;
      while (index < lines.length) {
        const line = lines[index];
        if (!line.trim()) {
          index++;
          continue;
        }
        if (tableStart(index)) {
          const header = cells(line);
          const rows: string[][] = [];
          index += 2;
          while (index < lines.length && lines[index].trim() && lines[index].includes('|')) rows.push(cells(lines[index++]));
          blocks.push(
            h('div', { class: 'cw-page-table-wrap', key: `table-${index}` }, [
              h('table', { class: 'cw-page-table' }, [
                h('thead', h('tr', header.map((cell, i) => h('th', { key: i }, inline(cell))))),
                h('tbody', rows.map((row, i) => h('tr', { key: i }, header.map((_, j) => h('td', { key: j }, inline(row[j] || '')))))),
              ]),
            ]),
          );
          continue;
        }
        if (/^#{1,6}\s/.test(line)) {
          blocks.push(h('h3', { key: index }, inline(line.replace(/^#+\s*/, ''))));
          index++;
          continue;
        }
        if (/^\s*```/.test(line)) {
          const start = index++;
          const code: string[] = [];
          while (index < lines.length && !/^\s*```/.test(lines[index])) code.push(lines[index++]);
          if (index < lines.length) index++;
          blocks.push(h('pre', { class: 'cw-page-code', key: start }, code.join('\n')));
          continue;
        }
        if (/^\s*[-*+]\s/.test(line)) {
          const start = index;
          const items: string[] = [];
          while (index < lines.length && /^\s*[-*+]\s/.test(lines[index])) items.push(lines[index++].replace(/^\s*[-*+]\s/, ''));
          blocks.push(h('ul', { key: start }, items.map((item, i) => h('li', { key: i }, inline(item)))));
          continue;
        }
        if (/^\s*>/.test(line)) {
          const start = index;
          const quote: string[] = [];
          while (index < lines.length && /^\s*>/.test(lines[index])) quote.push(lines[index++].replace(/^\s*>\s?/, ''));
          blocks.push(h('blockquote', { class: 'cw-page-quote', key: start }, inline(quote.join('\n'))));
          continue;
        }
        const start = index;
        const paragraph: string[] = [];
        while (
          index < lines.length &&
          lines[index].trim() &&
          !tableStart(index) &&
          !/^(?:#{1,6}\s|\s*```|\s*[-*+]\s|\s*>)/.test(lines[index])
        )
          paragraph.push(lines[index++]);
        blocks.push(h('p', { key: start }, inline(paragraph.join('\n'))));
      }
      return blocks;
    };
  },
});

const SourceMetadata = defineComponent({
  props: { metadata: { type: Object, default: () => ({}) } },
  setup(props) {
    return () => {
      const metadata = props.metadata || {};
      const doi = String(metadata.doi || '');
      const doiUrl = /^10\.\d{4,9}\/\S+$/i.test(doi) ? `https://doi.org/${doi}` : null;
      const authors = Array.isArray(metadata.authors) ? metadata.authors.join('、') : String(metadata.authors || '');
      if (!authors && !metadata.year && !doiUrl && !metadata.license_label && !metadata.processing_note) return null;
      return h('div', { class: 'cw-source-metadata' }, [
        h('span', [h(AppIcon, { icon: IconBook2, size: 14 }), authors + (metadata.year ? ` · ${metadata.year}` : '')]),
        doiUrl ? h('a', { href: doiUrl, target: '_blank', rel: 'noopener noreferrer' }, ['DOI: ' + doi, h(AppIcon, { icon: IconExternalLink, size: 11 })]) : null,
        metadata.license_label ? h('span', { class: 'cw-source-license' }, metadata.license_label) : null,
        metadata.processing_note
          ? h('details', [
              h('summary', '来源与处理说明'),
              h('p', metadata.citation || metadata.title),
              h('p', metadata.processing_note),
              metadata.license_notice ? h('p', metadata.license_notice) : null,
            ])
          : null,
      ]);
    };
  },
});

const SourcePages = defineComponent({
  props: { values: { type: Array, default: () => [] } },
  emits: ['locate'],
  setup(props, { emit }) {
    return () =>
      (props.values as number[]).length
        ? h('span', { class: 'cw-page-links' }, (props.values as number[]).map((number) =>
            h('button', { type: 'button', key: number, onClick: () => emit('locate', Number(number)) }, [`第 ${number} 页`, h(AppIcon, { icon: IconExternalLink, size: 11 })]),
          ))
        : h('span', { class: 'cw-reference-empty' }, '未标注来源页');
  },
});

const PageSelector = defineComponent({
  props: { pages: { type: Array, default: () => [] }, values: { type: Array, default: () => [] }, disabled: { type: Boolean, default: false } },
  emits: ['change'],
  setup(props, { emit }) {
    return () => {
      const values = (props.values as number[]) || [];
      return h('details', { class: 'cw-page-picker' }, [
        h('summary', values.length ? `选择来源页 (${values.length})` : '选择来源页'),
        h('div', (props.pages as any[]).map((page) =>
          h('label', { key: page.id }, [
            h('input', {
              type: 'checkbox',
              disabled: props.disabled,
              checked: values.includes(page.number),
              onChange: (e: Event) => {
                const checked = (e.target as HTMLInputElement).checked;
                emit('change', checked ? [...values, page.number].sort((a, b) => a - b) : values.filter((v) => v !== page.number));
              },
            }),
            `第 ${page.number} 页`,
          ]),
        )),
      ]);
    };
  },
});

const FieldResults = defineComponent({
  props: {
    schema: { type: Array, default: () => [] },
    fields: { type: Array, default: () => [] },
    members: { type: Array, default: () => [] },
    disabled: { type: Boolean, default: false },
  },
  emits: ['change', 'locate', 'image'],
  setup(props, { emit }) {
    const expanded = ref<string | null>(null);
    return () => {
      const schema = (props.schema as any[]) || [];
      const fields = (props.fields as any[]) || [];
      const members = (props.members as any[]) || [];
      const images = members.flatMap((page) => page.images || []);
      const update = (key: string, patch: any) => {
        const previous = fields.find((field) => field.key === key) || { key, value: null, status: 'not_found', evidence: '', source_pages: [], image_ids: [] };
        let next = { ...previous, ...patch };
        if (patch.status === 'not_found') next = { key, value: null, status: 'not_found', evidence: '', source_pages: [], image_ids: [] };
        if (patch.source_pages) {
          const allowed = new Set(members.filter((page) => patch.source_pages.includes(page.number)).flatMap((page) => page.images?.map((image: any) => image.id) || []));
          next.image_ids = (next.image_ids || []).filter((id: string) => allowed.has(id));
        }
        emit('change', [...fields.filter((field) => field.key !== key), next]);
      };
      return h('div', { class: 'cw-field-results' }, schema.map((definition, index) => {
        const record = fields.find((field) => field.key === definition.key);
        const field = record || { key: definition.key, value: null, status: 'not_found', evidence: '', source_pages: [], image_ids: [] };
        const found = field.status === 'found';
        const open = expanded.value === definition.key || (expanded.value === null && index === 0);
        const available = members.filter((page) => field.source_pages?.includes(page.number)).flatMap((page) => page.images || []);
        const value = typeof field.value === 'boolean' ? (field.value ? '是' : '否') : field.value == null ? '—' : String(field.value);
        return h('article', { class: `cw-field-result ${open ? 'open' : ''}`, key: definition.key }, [
          h('button', { type: 'button', class: 'cw-field-summary', onClick: () => { expanded.value = open ? '' : definition.key; } }, [
            h('span', [h('strong', definition.label), h('small', `${definition.key} · ${fieldTypeNames[definition.type]}`)]),
            h('span', { class: 'cw-field-summary-value' }, found ? value : '—'),
            h('span', { class: `cw-badge ${record ? (found ? 'approved' : 'pending') : 'pending'}` }, record ? (found ? '找到' : '未提取') : '尚未抽取'),
            h(AppIcon, { icon: IconChevronRight, size: 14 }),
          ]),
          open
            ? h('div', { class: 'cw-field-detail' }, [
                h('p', { class: 'cw-field-description' }, definition.description || '仅填写有原文证据的内容。'),
                h('div', { class: 'cw-field-value-row' }, [
                  h('label', [
                    h('span', '记录状态'),
                    h('select', {
                      value: field.status,
                      disabled: props.disabled,
                      onChange: (e: Event) => update(definition.key, { status: (e.target as HTMLSelectElement).value }),
                    }, [
                      h('option', { value: 'not_found' }, '未提取 · 留空'),
                      h('option', { value: 'found' }, '找到 · 有原文证据'),
                    ]),
                  ]),
                  h('label', [
                    h('span', definition.label),
                    definition.type === 'boolean'
                      ? h('select', {
                          'aria-label': definition.label,
                          value: field.value == null ? '' : String(field.value),
                          disabled: props.disabled || !found,
                          onChange: (e: Event) => {
                            const v = (e.target as HTMLSelectElement).value;
                            update(definition.key, { value: v === '' ? null : v === 'true' });
                          },
                        }, [
                          h('option', { value: '' }, '请选择'),
                          h('option', { value: 'true' }, '是'),
                          h('option', { value: 'false' }, '否'),
                        ])
                      : h('input', {
                          type: definition.type === 'number' ? 'number' : 'text',
                          step: definition.type === 'number' ? 'any' : undefined,
                          'aria-label': definition.label,
                          disabled: props.disabled || !found,
                          value: field.value == null ? '' : String(field.value),
                          placeholder: found ? '填写原文支持的值' : '未找到可靠依据，不补全',
                          onChange: (e: Event) => {
                            const v = (e.target as HTMLInputElement).value;
                            update(definition.key, { value: definition.type === 'number' ? (v === '' ? null : Number(v)) : v });
                          },
                        }),
                  ]),
                ]),
                found
                  ? [
                      h('label', { class: 'cw-evidence-field' }, [
                        h('span', '原文证据 · 原句摘录'),
                        h('textarea', {
                          rows: 3,
                          value: field.evidence || '',
                          disabled: props.disabled,
                          placeholder: '从来源页复制支持该字段的原文，不能写推测。',
                          onChange: (e: Event) => update(definition.key, { evidence: (e.target as HTMLTextAreaElement).value }),
                        }),
                      ]),
                      h('div', { class: 'cw-reference-row' }, [
                        h(SourcePages, { values: field.source_pages, onLocate: (n: number) => emit('locate', n) }),
                        h(PageSelector, { pages: members, values: field.source_pages, disabled: props.disabled, onChange: (source_pages: number[]) => update(definition.key, { source_pages }) }),
                      ]),
                      available.length > 0
                        ? h('details', { class: 'cw-field-image-picker' }, [
                            h('summary', `证据图像 (${field.image_ids?.length || 0})`),
                            h('div', available.map((image) =>
                              h('label', { key: image.id }, [
                                h('input', {
                                  type: 'checkbox',
                                  disabled: props.disabled,
                                  checked: field.image_ids?.includes(image.id) || false,
                                  onChange: (e: Event) => {
                                    const checked = (e.target as HTMLInputElement).checked;
                                    update(definition.key, { image_ids: checked ? [...(field.image_ids || []), image.id] : (field.image_ids || []).filter((id: string) => id !== image.id) });
                                  },
                                }),
                                h('span', image.caption || image.path?.split('/').pop()),
                                h('button', { type: 'button', onClick: () => emit('image', image) }, '看原图'),
                              ]),
                            )),
                          ])
                        : null,
                      field.image_ids?.length > 0
                        ? h('div', { class: 'cw-field-image-links' }, field.image_ids.map((id: string) => {
                            const image = images.find((item) => item.id === id);
                            return image
                              ? h('button', { type: 'button', key: id, onClick: () => emit('image', image) }, [
                                  h('img', { src: image.url, alt: image.caption || '来源图片', loading: 'lazy' }),
                                  h('span', image.caption || image.path?.split('/').pop()),
                                ])
                              : null;
                          }))
                        : null,
                    ]
                  : h('p', { class: 'cw-field-null-note' }, '该字段保留 null；不从其他论文、常识或图像猜测补全。'),
              ])
            : null,
        ]);
      }));
    };
  },
});

const ChapterProducts = defineComponent({
  props: {
    editor: { type: Object, default: () => ({}) },
    result: { type: Object, default: () => ({}) },
    document: { type: Object, default: () => ({}) },
    members: { type: Array, default: () => [] },
    pdfs: { type: Array, default: () => [] },
    productTab: { type: String, default: 'markdown' },
    markdownMode: { type: String, default: 'preview' },
    onSchema: { type: Boolean, default: false },
    disabled: { type: Boolean, default: false },
  },
  emits: ['update:productTab', 'update:markdownMode', 'update:editor', 'locate', 'image', 'schema'],
  setup(props, { emit }) {
    return () => {
      const members = (props.members as any[]) || [];
      const images = members.flatMap((page) => page.images || []);
      const result = (props.result as any) || {};
      const schema = result.extraction_schema || [];
      const currentSchema = (props.document as any)?.extraction_schema || [];
      const editor = (props.editor as any) || {};
      const fields = editor.fields || [];
      const interpretation = editor.interpretation || { text: '', source_pages: [] };
      const found = fields.filter((field: any) => field.status === 'found').length;
      const schemaChanged = (result.schema_revision || 0) !== ((props.document as any)?.schema_revision || 0);
      const patch = (values: any) => emit('update:editor', { ...editor, ...values });

      return h('div', { class: 'cw-products' }, [
        h('div', { class: 'cw-product-tabs', role: 'tablist', 'aria-label': '章节产物类型' }, [
          h('button', { key: 'markdown', role: 'tab', 'aria-selected': props.productTab === 'markdown', class: props.productTab === 'markdown' ? 'active' : '', onClick: () => emit('update:productTab', 'markdown') }, [
            h(AppIcon, { icon: IconFileText, size: 16 }),
            '图文 Markdown',
          ]),
          h('button', { key: 'fields', role: 'tab', 'aria-selected': props.productTab === 'fields', class: props.productTab === 'fields' ? 'active' : '', onClick: () => emit('update:productTab', 'fields') }, [
            h(AppIcon, { icon: IconTable, size: 16 }),
            '字段提取',
            schema.length > 0 ? h('small', schema.length) : null,
          ]),
        ]),
        props.productTab === 'markdown'
          ? [
              h('div', { class: 'cw-markdown-toolbar' }, [
                h('span', '正文与原始图像链接'),
                h('div', { class: 'cw-segment' }, [
                  h('button', { type: 'button', class: props.markdownMode === 'preview' ? 'active' : '', onClick: () => emit('update:markdownMode', 'preview') }, '预览'),
                  h('button', { type: 'button', class: props.markdownMode === 'edit' ? 'active' : '', onClick: () => emit('update:markdownMode', 'edit') }, '编辑'),
                ]),
              ]),
              props.markdownMode === 'preview'
                ? h('div', { class: 'cw-markdown-preview' }, h(PageMarkdown, { text: editor.text, images, pdfs: props.pdfs, onPageLink: (n: number) => emit('locate', n), onImage: (img: any) => emit('image', img) }))
                : [
                    h('textarea', {
                      class: 'cw-main-editor cw-markdown-editor',
                      value: editor.text,
                      readOnly: props.disabled,
                      'aria-label': '编辑本章图文Markdown',
                      spellcheck: false,
                      onChange: (e: Event) => patch({ text: (e.target as HTMLTextAreaElement).value }),
                    }),
                    images.length > 0
                      ? h('details', { class: 'cw-insert-images' }, [
                          h('summary', `插入本章原始图片 (${images.length})`),
                          h('div', images.map((image) =>
                            h('button', { type: 'button', key: image.id, disabled: props.disabled, onClick: () => patch({ text: `${editor.text}\n\n![${(image.caption || '原始图片').replace(/[\[\]\n]/g, ' ')}](${image.url})\n` }) }, [
                              h(AppIcon, { icon: IconPhoto, size: 14 }),
                              image.caption || image.path?.split('/').pop(),
                            ]),
                          )),
                        ])
                      : null,
                  ],
              h('section', { class: 'cw-interpretation' }, [
                h('header', [h('h3', '独立章节解读'), h('span', '与原文正文分开保存')]),
                h('p', '用于解释本章内容、意义与局限；不是原文摘要，也不替代原文事实。'),
                h('textarea', {
                  rows: 3,
                  value: interpretation.text,
                  readOnly: props.disabled,
                  'aria-label': '编辑独立章节解读',
                  placeholder: '目前没有独立解读。填写时说明依据和局限，并选择出处页。',
                  onChange: (e: Event) => patch({ interpretation: { ...interpretation, text: (e.target as HTMLTextAreaElement).value } }),
                }),
                h('div', { class: 'cw-reference-row' }, [
                  h(SourcePages, { values: interpretation.source_pages, onLocate: (n: number) => emit('locate', n) }),
                  h(PageSelector, { pages: members, values: interpretation.source_pages, disabled: props.disabled, onChange: (source_pages: number[]) => patch({ interpretation: { ...interpretation, source_pages } }) }),
                ]),
              ]),
            ]
          : h('div', { class: 'cw-extraction' }, [
              h('div', { class: 'cw-extraction-heading' }, [
                h('span', schema.length ? `${schema.length} 个字段 · ${found} 找到 · ${fields.filter((field: any) => field.status === 'not_found').length} 未提取` : '先设置要从文献提取的字段'),
                props.onSchema ? h('button', { type: 'button', onClick: () => emit('schema') }, [h(AppIcon, { icon: IconSettings, size: 14 }), '编辑字段模板']) : null,
              ]),
              schemaChanged
                ? h('div', { class: 'cw-alert warning' }, `当前模板为第 ${(props.document as any)?.schema_revision || 0} 版，以下结果使用第 ${result.schema_revision || 0} 版。重新整理后才会应用新模板。`)
                : null,
              schema.length
                ? [
                    h('p', { class: 'cw-extraction-note' }, '只填原文有可靠依据的字段；未提及或有歧义时留空。展开字段可修改值、核对原句和定位来源。'),
                    h(FieldResults, { schema, fields, members, onChange: (next: any[]) => patch({ fields: next }), onLocate: (n: number) => emit('locate', n), onImage: (img: any) => emit('image', img), disabled: props.disabled }),
                  ]
                : h('div', { class: 'cw-fields-empty' }, [
                    h(AppIcon, { icon: IconTable, size: 30 }),
                    h('strong', currentSchema.length ? '已设置模板，当前版本尚未提取字段' : '定义你需要的植物性状'),
                    h('p', currentSchema.length ? `当前模板包含 ${currentSchema.length} 个字段。配置真实模型后重新整理，生成有原文证据的字段结果。` : '添加学名、叶形、生境等字段，并说明字段含义。模板决定后续模型抽取内容。'),
                    props.onSchema
                      ? h('button', { type: 'button', class: 'cw-button', onClick: () => emit('schema') }, [h(AppIcon, { icon: IconSettings, size: 18 }), h('span', currentSchema.length ? '查看字段模板' : '设置字段模板')])
                      : null,
                  ]),
            ]),
      ]);
    };
  },
});

const PdfUploadDialog = defineComponent({
  props: { document: { type: Object, default: () => ({}) } },
  emits: ['close', 'saved'],
  setup(props, { emit }) {
    const file = ref<File | null>(null);
    const busy = ref(false);
    const error = ref('');
    async function submit(event: Event): Promise<void> {
      event.preventDefault();
      if (!file.value) return;
      busy.value = true;
      error.value = '';
      try {
        if (file.value.size > 16 * 1048576) throw Error('原始PDF限16 MiB，请压缩后再上传。');
        await request('/documents/' + encodeURIComponent((props.document as any).id) + '/pdfs', { name: file.value.name, content_base64: await encodeFile(file.value), select: true });
        emit('saved');
      } catch (e) {
        error.value = e instanceof Error ? e.message : String(e);
      } finally {
        busy.value = false;
      }
    }
    return () =>
      h(CwDialog, { title: '添加原始PDF', subtitle: '保留实际PDF原件，方便逐页核对。可为同一文献添加多个PDF。', onClose: () => emit('close') }, {
        default: () =>
          h('form', { onSubmit: submit }, [
            h('label', { class: 'cw-upload-box' }, [
              h(AppIcon, { icon: IconUpload, size: 30 }),
              h('strong', file.value ? file.value.name : '选择文献原始PDF'),
              h('span', file.value ? byteSize(file.value.size) : '单文件不超过16 MiB'),
              h('input', { type: 'file', accept: '.pdf,application/pdf', 'aria-label': '选择原始PDF', onChange: (e: Event) => { file.value = (e.target as HTMLInputElement).files?.[0] || null; error.value = ''; } }),
            ]),
            error.value ? h('div', { class: 'cw-alert error' }, error.value) : null,
            h('footer', { class: 'cw-modal-footer' }, [
              h('button', { type: 'button', class: 'cw-button', disabled: busy.value, onClick: () => emit('close') }, [h('span', '取消')]),
              h('button', { type: 'submit', class: 'cw-button primary', disabled: busy.value || !file.value }, [h('span', busy.value ? '正在保存…' : '添加并打开PDF')]),
            ]),
          ]),
      });
  },
});

const PageMappingDialog = defineComponent({
  props: { document: { type: Object, default: () => ({}) }, sourcePage: { type: Number, default: 1 } },
  emits: ['close', 'saved'],
  setup(props, { emit }) {
    const offset = ref(String((props.document as any)?.pdf_page_offset || 0));
    const busy = ref(false);
    const error = ref('');
    async function submit(event: Event): Promise<void> {
      event.preventDefault();
      const value = Number(offset.value);
      if (!Number.isInteger(value)) {
        error.value = '偏移量必须是整数。';
        return;
      }
      busy.value = true;
      error.value = '';
      try {
        await request('/documents/' + encodeURIComponent((props.document as any).id), { pdf_page_offset: value });
        emit('saved');
      } catch (e) {
        error.value = e instanceof Error ? e.message : String(e);
      } finally {
        busy.value = false;
      }
    }
    return () =>
      h(CwDialog, { title: '校正PDF页码对应', subtitle: '有封面或前言时，可手动指定PDF与OCR的页码偏移。系统不会推断对应关系。', onClose: () => emit('close') }, {
        default: () =>
          h('form', { onSubmit: submit }, [
            h('label', { class: 'cw-field' }, [
              h('span', 'PDF页码偏移量'),
              h('input', { type: 'number', step: '1', value: offset.value, required: true, onInput: (e: Event) => { offset.value = (e.target as HTMLInputElement).value; } }),
              h('small', 'PDF页码 = OCR页码 + 偏移量。例如OCR第1页位于PDF第3页，填写2。'),
            ]),
            h('div', { class: 'cw-alert' }, `当前来源：OCR第 ${props.sourcePage} 页 → PDF第 ${Number(props.sourcePage) + Number(offset.value || 0)} 页`),
            error.value ? h('div', { class: 'cw-alert error' }, error.value) : null,
            h('footer', { class: 'cw-modal-footer' }, [
              h('button', { type: 'button', class: 'cw-button', disabled: busy.value, onClick: () => emit('close') }, [h('span', '取消')]),
              h('button', { type: 'submit', class: 'cw-button primary', disabled: busy.value }, [h('span', '保存对应关系')]),
            ]),
          ]),
      });
  },
});

const RevisionHistory = defineComponent({
  props: { chapterId: { type: String, default: '' }, revision: { type: Number, default: 0 }, status: { type: String, default: '' } },
  emits: ['locate'],
  setup(props, { emit }) {
    const history = ref<any[]>([]);
    const error = ref('');
    async function load(): Promise<void> {
      try {
        const value = await request('/history/' + encodeURIComponent(props.chapterId));
        history.value = value.history || [];
        error.value = '';
      } catch (e) {
        error.value = e instanceof Error ? e.message : String(e);
      }
    }
    onMounted(load);
    watch(() => [props.chapterId, props.revision, props.status], load);
    const names: Record<string, string> = {
      prepared_example: '载入真实论文预置整理',
      codex_prepared_example: '载入真实论文预置整理',
      run_requested: '提交AI整理',
      generated: 'AI整理完成',
      manual_revised: '保存人工修改',
      approved: '确认使用',
      rejected: '暂不采用',
      manual_revision: '保存人工修改',
      manual_edit: '保存人工修改',
      revise: '保存人工修改',
      model: 'AI重新整理',
      model_completed: 'AI整理完成',
      demo: '演示整理',
      approve: '确认使用',
      reject: '暂不采用',
      review: '人工确认',
    };
    return () =>
      h('div', { class: 'cw-revision-history' }, [
        h('h3', '本章修改记录'),
        h('p', { class: 'cw-history-hint' }, '展开记录可回看当时的正文和修改要求，历史快照保留原样。'),
        error.value
          ? h('p', { class: 'cw-quiet' }, error.value)
          : history.value.length
            ? h('ul', history.value.map((item) => {
                const payload = item.payload || {};
                const corpus = payload.corpus || payload.result?.corpus;
                const previous = payload.previous_result?.corpus;
                const instructions = payload.instructions || payload.result?.instructions;
                const samples = payload.sft_samples || payload.result?.sft_samples || [];
                return h('li', { class: 'cw-history-item', key: item.id }, [
                  h('details', { class: 'cw-history-entry' }, [
                    h('summary', [
                      h('span', `第 ${item.revision} 版`),
                      h('strong', names[item.event] || item.event),
                      h('small', dateLabel(item.created_at)),
                      h(AppIcon, { icon: IconChevronRight, size: 14 }),
                    ]),
                    h('div', { class: 'cw-history-payload' }, [
                      instructions ? h('section', [h('h4', '告诉AI的修改要求'), h('p', { class: 'cw-history-instruction' }, instructions)]) : null,
                      item.event === 'run_requested' && !instructions ? h('p', { class: 'cw-history-hint' }, '按原始OCR内容重新整理，没有附加修改要求。') : null,
                      corpus ? h('section', [h('h4', '本次正文快照'), h('pre', corpus)]) : null,
                      previous
                        ? h('section', [
                            h('h4', `${item.event === 'run_requested' ? '提交时的上版正文' : '修改前正文'}${payload.previous_revision != null ? ` · 第 ${payload.previous_revision} 版` : ''}`),
                            h('pre', previous),
                          ])
                        : null,
                      h(HistoryProductSnapshot, { result: payload.result || (item.event === 'run_requested' ? null : payload), label: '本次产物', onLocate: (n: number) => emit('locate', n) }),
                      h(HistoryProductSnapshot, { result: payload.previous_result, label: '上版产物', onLocate: (n: number) => emit('locate', n) }),
                      samples.length > 0
                        ? h('section', [
                            h('h4', `当时的问答候选 · ${samples.length} 组`),
                            samples.map((sample: any, index: number) =>
                              h('div', { class: 'cw-history-question', key: index }, [
                                h('p', [h('strong', '问题：'), sample.question]),
                                h('p', [h('strong', '答案：'), sample.answer]),
                                h('small', `来源页：${sample.source_pages?.join('、') || '未标注'}`),
                              ]),
                            ),
                          ])
                        : null,
                      !corpus && !previous && !instructions && item.event !== 'run_requested'
                        ? h('p', { class: 'cw-history-hint' }, '该记录没有正文快照。')
                        : null,
                    ]),
                  ]),
                ]);
              }))
            : h('p', { class: 'cw-quiet' }, '保存修改或重新整理后，会在这里保留版本记录。'),
      ]);
  },
});

const HistoryProductSnapshot = defineComponent({
  props: { result: { type: Object, default: null }, label: { type: String, default: '' } },
  emits: ['locate'],
  setup(props, { emit }) {
    return () => {
      const result = props.result;
      if (!result) return null;
      const interpretation = interpretationOf(result);
      const fields = result.structured_fields || [];
      const labels = new Map((result.extraction_schema || []).map((field: any) => [field.key, field.label]));
      const sections: any[] = [];
      if (interpretation.text)
        sections.push(
          h('section', [
            h('h4', `${props.label} · 独立解读`),
            h('pre', interpretation.text),
            h(SourcePages, { values: interpretation.source_pages, onLocate: (n: number) => emit('locate', n) }),
          ]),
        );
      if (fields.length > 0)
        sections.push(
          h('section', [
            h('h4', `${props.label} · 字段快照${result.schema_revision != null ? `（模板第 ${result.schema_revision} 版）` : ''}`),
            h('div', { class: 'cw-history-field-snapshots' }, fields.map((field: any) =>
              h('article', { key: field.key }, [
                h('header', [h('strong', labels.get(field.key) || field.key), h('span', { class: `cw-badge ${field.status === 'found' ? 'approved' : 'pending'}` }, field.status === 'found' ? '找到' : '未提取')]),
                h('p', [h('b', '值：'), field.value == null ? 'null' : typeof field.value === 'boolean' ? (field.value ? '是' : '否') : String(field.value)]),
                field.evidence ? h('p', [h('b', '原文证据：'), field.evidence]) : null,
                h(SourcePages, { values: field.source_pages, onLocate: (n: number) => emit('locate', n) }),
                field.image_ids?.length > 0 ? h('small', `来源图像：${field.image_ids.join('、')}`) : null,
              ]),
            )),
          ]),
        );
      return sections;
    };
  },
});

const ChapterImportDialog = defineComponent({
  props: { context: { type: Object, default: () => ({}) } },
  emits: ['close', 'imported'],
  setup(props, { emit }) {
    const mode = ref<'folder' | 'zip'>('folder');
    const files = ref<File[]>([]);
    const name = ref('');
    const purpose = ref('both');
    const license = ref('unknown');
    const error = ref('');
    const busy = ref(false);
    const total = computed(() => files.value.reduce((sum, file) => sum + file.size, 0));

    function choose(list: FileList | File[]): void {
      const selected = Array.from(list || []);
      error.value = '';
      if (selected.length > 2000) {
        error.value = '每篇文献最多 2000 个文件。';
        return;
      }
      if (selected.some((file) => file.size > 16 * 1048576)) {
        error.value = '单个文件限 16 MiB，请拆分大文件。';
        return;
      }
      if (selected.reduce((sum, file) => sum + file.size, 0) > 32 * 1048576) {
        error.value = '本次导入总量限 32 MiB，请按文献拆分。';
        return;
      }
      files.value = selected;
      if (selected.length) name.value = mode.value === 'folder' ? selected[0].webkitRelativePath.split('/')[0] || '导入文献' : selected[0].name.replace(/\.zip$/i, '');
    }

    async function submit(event: Event): Promise<void> {
      event.preventDefault();
      busy.value = true;
      error.value = '';
      try {
        const body: any = { name: name.value.trim() || '导入文献', purpose: purpose.value, license: license.value, record_id: props.context?.record_id, source_id: props.context?.source_id };
        if (mode.value === 'folder') {
          body.files = await Promise.all(files.value.map(async (file) => ({ path: file.webkitRelativePath || file.name, content_base64: await encodeFile(file) })));
        } else {
          body.content_base64 = await encodeFile(files.value[0]);
        }
        const outcome = await request('/import', body);
        emit('imported', outcome);
      } catch (e) {
        error.value = e instanceof Error ? e.message : String(e);
      } finally {
        busy.value = false;
      }
    }

    return () =>
      h(CwDialog, { title: '导入文献', subtitle: props.context?.record_name ? `保存到 ${props.context.source_name} / ${props.context.record_name}` : '将文献归入当前物种 / 数据条目。保留原始 PDF、逐页文字与图片。', onClose: () => emit('close') }, {
        default: () =>
          h('form', { onSubmit: submit }, [
            h('div', { class: 'cw-modal-body' }, [
              h('div', { class: 'cw-tabs' }, [
                h('button', { type: 'button', class: mode.value === 'folder' ? 'active' : '', onClick: () => { mode.value = 'folder'; files.value = []; name.value = ''; error.value = ''; } }, '文献文件夹'),
                h('button', { type: 'button', class: mode.value === 'zip' ? 'active' : '', onClick: () => { mode.value = 'zip'; files.value = []; name.value = ''; error.value = ''; } }, 'OCR 结果 ZIP'),
              ]),
              h('label', { class: 'cw-upload-box' }, [
                h(AppIcon, { icon: mode.value === 'folder' ? IconFolder : IconUpload, size: 32 }),
                h('strong', mode.value === 'folder' ? '选择一篇文献的资料文件夹' : '选择逐页 OCR 结果 ZIP'),
                h('span', mode.value === 'folder' ? '逐页 .md / .json ＋ images 图片 ＋ 原始 PDF（可选）' : '保留目录结构，图片路径与页文档保持一致'),
                h('small', '单文件 ≤ 16 MiB · 总量 ≤ 32 MiB · 最多 2000 文件'),
                mode.value === 'folder'
                  ? h('input', { key: 'folder', 'aria-label': '选择文献文件夹', type: 'file', webkitdirectory: '', directory: '', multiple: true, onChange: (e: Event) => choose((e.target as HTMLInputElement).files as FileList) })
                  : h('input', { key: 'zip', 'aria-label': '选择OCR资料ZIP', type: 'file', accept: '.zip', onChange: (e: Event) => choose((e.target as HTMLInputElement).files as FileList) }),
              ]),
              files.value.length > 0
                ? h('div', { class: 'cw-file-summary' }, [
                    h('span', [h(AppIcon, { icon: IconCheck, size: 15 }), `${files.value.length} 个文件已选择`]),
                    h('strong', byteSize(total.value)),
                    h('div', [
                      files.value.slice(0, 4).map((file, i) => h('p', { key: i }, file.webkitRelativePath || file.name)),
                      files.value.length > 4 ? h('small', `另有 ${files.value.length - 4} 个文件`) : null,
                    ]),
                  ])
                : null,
              h('label', { class: 'cw-field' }, [
                h('span', '文献名称'),
                h('input', { value: name.value, maxLength: 150, required: true, placeholder: '例如：水稻光合作用研究资料', onInput: (e: Event) => { name.value = (e.target as HTMLInputElement).value; } }),
              ]),
              h('div', { class: 'cw-field-grid' }, [
                h('label', { class: 'cw-field' }, [
                  h('span', '计划用途'),
                  h('select', { value: purpose.value, onChange: (e: Event) => { purpose.value = (e.target as HTMLSelectElement).value; } }, [
                    h('option', { value: 'both' }, '训练数据 + 知识库'),
                    h('option', { value: 'training' }, '仅训练数据'),
                    h('option', { value: 'kb' }, '仅知识库'),
                  ]),
                ]),
                h('label', { class: 'cw-field' }, [
                  h('span', '授权范围'),
                  h('select', { value: license.value, onChange: (e: Event) => { license.value = (e.target as HTMLSelectElement).value; } }, [
                    h('option', { value: 'unknown' }, '用途待核对'),
                    h('option', { value: 'self_owned' }, '自有 / 已获授权'),
                    h('option', { value: 'research_only' }, '仅内部知识库'),
                  ]),
                ]),
              ]),
              h('div', { class: 'cw-alert' }, [
                h(AppIcon, { icon: IconFileText, size: 16 }),
                h('span', '这里接收已有 OCR 结果，不重新识别 PDF。页标题、原文和实际图片保留为输入；模型加工与人工修订另行保存。'),
              ]),
              error.value ? h('div', { class: 'cw-alert error', role: 'alert' }, [h(AppIcon, { icon: IconAlertTriangle, size: 16 }), error.value]) : null,
            ]),
            h('footer', { class: 'cw-modal-footer' }, [
              h('button', { type: 'button', class: 'cw-button', disabled: busy.value, onClick: () => emit('close') }, [h('span', '取消')]),
              h('button', { type: 'submit', class: 'cw-button primary', disabled: busy.value || !files.value.length || !name.value.trim() }, [
                busy.value ? h(AppIcon, { icon: IconLoader2, size: 18 }) : h(AppIcon, { icon: IconFolder, size: 18 }),
                h('span', busy.value ? '正在导入并建立逐页记录…' : '导入并预览'),
              ]),
            ]),
          ]),
      });
  },
});

const ModelDialog = defineComponent({
  props: { model: { type: Object, default: () => ({}) } },
  emits: ['close', 'saved'],
  setup(props, { emit }) {
    const endpoint = ref((props.model as any)?.endpoint || '');
    const name = ref((props.model as any)?.model || '');
    const key = ref('');
    const vision = ref(!!(props.model as any)?.vision);
    const clearKey = ref(false);
    const busy = ref(false);
    const error = ref('');
    async function submit(event: Event): Promise<void> {
      event.preventDefault();
      busy.value = true;
      error.value = '';
      try {
        await request('/model', {
          endpoint: endpoint.value.trim(),
          model: name.value.trim(),
          vision: vision.value,
          ...(clearKey.value ? { key: null } : key.value.trim() ? { key: key.value.trim() } : {}),
        });
        emit('saved');
      } catch (e) {
        error.value = e instanceof Error ? e.message : String(e);
      } finally {
        busy.value = false;
      }
    }
    return () =>
      h(CwDialog, { title: '模型服务设置', subtitle: '真实加工会调用你指定的服务，并把原始章节内容发送给该端点。', onClose: () => emit('close') }, {
        default: () =>
          h('form', { onSubmit: submit }, [
            h('div', { class: 'cw-modal-body' }, [
              h('div', { class: 'cw-model-notice' }, [
                h(AppIcon, { icon: IconSettings, size: 18 }),
                h('div', [h('strong', 'OpenAI 兼容接口'), h('p', '填写 API 基础地址和模型 ID。由你选择模型与服务，本系统不会默认调用其他供应商。')]),
              ]),
              h('label', { class: 'cw-field' }, [
                h('span', 'API 基础地址'),
                h('input', { type: 'url', required: true, value: endpoint.value, placeholder: 'https://你的服务地址/v1', autocomplete: 'off', onInput: (e: Event) => { endpoint.value = (e.target as HTMLInputElement).value; } }),
                h('small', '填写基础地址；服务需支持 chat completions 接口。'),
              ]),
              h('label', { class: 'cw-field' }, [
                h('span', '模型 ID'),
                h('input', { value: name.value, required: true, maxLength: 200, placeholder: '填写服务提供的准确模型名称', onInput: (e: Event) => { name.value = (e.target as HTMLInputElement).value; } }),
              ]),
              h('label', { class: 'cw-field' }, [
                h('span', ['API 密钥 ', (props.model as any)?.has_key ? h('span', { class: 'cw-badge approved' }, '本机已保存') : null]),
                h('input', {
                  type: 'password',
                  value: key.value,
                  placeholder: (props.model as any)?.has_key ? '留空保留已保存密钥' : '输入服务所需的密钥',
                  autocomplete: 'new-password',
                  disabled: clearKey.value,
                  onInput: (e: Event) => { key.value = (e.target as HTMLInputElement).value; clearKey.value = false; },
                }),
                h('small', '密钥不会回显到页面。仅配置地址与模型，不代表服务已经验证可用。'),
              ]),
              (props.model as any)?.has_key
                ? h('label', { class: 'cw-checkbox' }, [
                    h('input', { type: 'checkbox', checked: clearKey.value, onChange: (e: Event) => { clearKey.value = (e.target as HTMLInputElement).checked; } }),
                    '保存时清除本机现有密钥',
                  ])
                : null,
              h('label', { class: 'cw-checkbox cw-vision' }, [
                h('input', { type: 'checkbox', checked: vision.value, onChange: (e: Event) => { vision.value = (e.target as HTMLInputElement).checked; } }),
                h('span', [h('strong', '包含章节图片'), h('small', '仅当所选模型支持图像输入时开启；文本模型请关闭。')]),
              ]),
              h('div', { class: 'cw-alert warning' }, [
                h(AppIcon, { icon: IconLock, size: 16 }),
                h('span', `请核对文献授权与模型 API 使用条件。真实运行可能产生费用，并将所选章节文本${vision.value ? '及图片' : ''}发送到上述端点。`),
              ]),
              error.value ? h('div', { class: 'cw-alert error', role: 'alert' }, error.value) : null,
            ]),
            h('footer', { class: 'cw-modal-footer' }, [
              h('button', { type: 'button', class: 'cw-button', disabled: busy.value, onClick: () => emit('close') }, [h('span', '取消')]),
              h('button', { type: 'submit', class: 'cw-button primary', disabled: busy.value || !endpoint.value.trim() || !name.value.trim() }, [
                h(AppIcon, { icon: IconCheck, size: 18 }),
                h('span', busy.value ? '正在保存…' : '保存模型设置'),
              ]),
            ]),
          ]),
      });
  },
});

const MetadataDialog = defineComponent({
  props: { document: { type: Object, default: () => ({}) } },
  emits: ['close', 'saved'],
  setup(props, { emit }) {
    const title = ref((props.document as any)?.title || (props.document as any)?.name || '');
    const purpose = ref((props.document as any)?.purpose || 'both');
    const license = ref((props.document as any)?.license || 'unknown');
    const busy = ref(false);
    const error = ref('');
    async function submit(event: Event): Promise<void> {
      event.preventDefault();
      busy.value = true;
      error.value = '';
      try {
        await request('/documents/' + encodeURIComponent((props.document as any).id), { title: title.value.trim(), purpose: purpose.value, license: license.value });
        emit('saved');
      } catch (e) {
        error.value = e instanceof Error ? e.message : String(e);
      } finally {
        busy.value = false;
      }
    }
    return () =>
      h(CwDialog, { title: '文献用途设置', subtitle: '保留用途确认记录，并同步已派生的章节语料资产。', onClose: () => emit('close') }, {
        default: () =>
          h('form', { onSubmit: submit }, [
            h('div', { class: 'cw-modal-body' }, [
              h('label', { class: 'cw-field' }, [
                h('span', '文献名称'),
                h('input', { required: true, maxLength: 150, value: title.value, onInput: (e: Event) => { title.value = (e.target as HTMLInputElement).value; } }),
              ]),
              h('div', { class: 'cw-field-grid' }, [
                h('label', { class: 'cw-field' }, [
                  h('span', '计划用途'),
                  h('select', { value: purpose.value, onChange: (e: Event) => { purpose.value = (e.target as HTMLSelectElement).value; } }, Object.entries(purposeNames).map(([k, label]) => h('option', { value: k, key: k }, label))),
                ]),
                h('label', { class: 'cw-field' }, [
                  h('span', '授权范围'),
                  h('select', { value: license.value, onChange: (e: Event) => { license.value = (e.target as HTMLSelectElement).value; } }, Object.entries(licenseNames).map(([k, label]) => h('option', { value: k, key: k }, label))),
                ]),
              ]),
              h('div', { class: 'cw-alert' }, [
                h(AppIcon, { icon: IconShieldCheck, size: 16 }),
                h('span', '用途待核对时保留原文和加工结果，待确认授权后再用于产出。选择标签需要基于你的授权依据，系统不会自动判断版权或 API 条款。'),
              ]),
              error.value ? h('div', { class: 'cw-alert error', role: 'alert' }, error.value) : null,
            ]),
            h('footer', { class: 'cw-modal-footer' }, [
              h('button', { type: 'button', class: 'cw-button', disabled: busy.value, onClick: () => emit('close') }, [h('span', '取消')]),
              h('button', { type: 'submit', class: 'cw-button primary', disabled: busy.value || !title.value.trim() }, [
                h(AppIcon, { icon: IconCheck, size: 18 }),
                h('span', busy.value ? '正在保存…' : '保存用途设置'),
              ]),
            ]),
          ]),
      });
  },
});

const ChapterEditor = defineComponent({
  props: { detail: { type: Object, default: () => ({}) } },
  emits: ['close', 'saved'],
  setup(props, { emit }) {
    const drafts = ref(((props.detail as any)?.chapters || []).map((ch: any) => ({ title: ch.title, page_ids: [...(ch.page_ids || [])] })));
    const active = ref(0);
    const busy = ref(false);
    const error = ref('');
    const pages = computed(() => (props.detail as any)?.pages || []);
    const assigned = computed(() => new Set(drafts.value.flatMap((d: any) => d.page_ids)));
    const missing = computed(() => pages.value.filter((p: any) => !assigned.value.has(p.id)).length);
    const selected = computed(() => drafts.value[active.value]);

    function setTitle(value: string): void {
      drafts.value = drafts.value.map((ch: any, i: number) => (i === active.value ? { ...ch, title: value } : ch));
    }
    function assign(pageId: string, checked: boolean): void {
      drafts.value = drafts.value.map((ch: any, i: number) => ({
        ...ch,
        page_ids: i === active.value && checked ? [...new Set([...ch.page_ids, pageId])] : ch.page_ids.filter((id: string) => id !== pageId),
      }));
    }
    function add(): void {
      drafts.value = [...drafts.value, { title: `新章节 ${drafts.value.length + 1}`, page_ids: [] }];
      active.value = drafts.value.length;
    }
    function remove(index: number): void {
      if (drafts.value.length <= 1) return;
      const removed = drafts.value[index];
      const next = drafts.value.filter((_: any, i: number) => i !== index);
      next[0] = { ...next[0], page_ids: [...new Set([...next[0].page_ids, ...removed.page_ids])] };
      drafts.value = next;
      active.value = 0;
    }
    async function save(): Promise<void> {
      busy.value = true;
      error.value = '';
      try {
        await request('/rebuild', {
          document_id: (props.detail as any).document.id,
          chapters: drafts.value.map((d: any) => ({ title: d.title.trim(), page_ids: d.page_ids })),
        });
        emit('saved');
      } catch (e) {
        error.value = e instanceof Error ? e.message : String(e);
      } finally {
        busy.value = false;
      }
    }

    return () =>
      h(CwDialog, { title: '章节编排', subtitle: '编辑章节标题并指定原始页归属；同一页只归属一个章节。', wide: true, onClose: () => emit('close') }, {
        default: () =>
          h('div', { class: 'cw-modal-body' }, [
            h('div', { class: 'cw-editor-layout' }, [
              h('div', { class: 'cw-draft-list' }, [
                h('div', { class: 'cw-rail-heading' }, [
                  h('strong', `${drafts.value.length} 个章节`),
                  h('button', { type: 'button', class: 'cw-text-button', onClick: add }, [h(AppIcon, { icon: IconPlus, size: 15 }), '新增']),
                ]),
                drafts.value.map((ch: any, i: number) =>
                  h('div', { class: `cw-draft-item ${i === active.value ? 'active' : ''}`, key: i }, [
                    h('button', { type: 'button', onClick: () => { active.value = i; } }, [
                      h('span', String(i + 1).padStart(2, '0')),
                      h('strong', ch.title || '未命名章节'),
                      h('small', `${ch.page_ids.length} 页`),
                    ]),
                    h('button', { type: 'button', class: 'cw-icon-button', 'aria-label': `删除章节 ${ch.title}`, disabled: drafts.value.length <= 1, onClick: () => remove(i) }, [h(AppIcon, { icon: IconTrash, size: 15 })]),
                  ]),
                ),
              ]),
              selected.value
                ? h('div', { class: 'cw-page-assignment-panel' }, [
                    h('label', { class: 'cw-field' }, [
                      h('span', '章节标题'),
                      h('input', { value: selected.value.title, maxLength: 200, 'aria-label': '当前章节标题', onInput: (e: Event) => setTitle((e.target as HTMLInputElement).value) }),
                    ]),
                    h('div', { class: 'cw-assignment-heading' }, [h('strong', '原始页归属'), h('span', `${selected.value.page_ids.length} 页已选择`)]),
                    h('div', { class: 'cw-page-assignments' }, pages.value.map((page: any) => {
                      const owner = drafts.value.findIndex((d: any) => d.page_ids.includes(page.id));
                      return h('label', { class: 'cw-page-option', key: page.id }, [
                        h('input', { type: 'checkbox', checked: selected.value.page_ids.includes(page.id), onChange: (e: Event) => assign(page.id, (e.target as HTMLInputElement).checked) }),
                        h('span', [h('strong', `第 ${page.number} 页`), h('small', String(page.text || '无文字内容').replace(/[#\n]/g, ' ').slice(0, 76))]),
                        h('span', { class: 'cw-page-option-meta' }, [`${page.images?.length || 0} 图`, h('small', owner >= 0 ? `章节 ${owner + 1}` : '未分配')]),
                      ]);
                    })),
                  ])
                : null,
            ]),
            h('div', { class: 'cw-alert warning' }, [
              h(AppIcon, { icon: IconAlertTriangle, size: 16 }),
              h('span', `${missing.value ? `还有 ${missing.value} 页未分配。` : '全部原始页已归属章节。'} 修改编排后，受影响的旧语料需要重新加工与复核。删除章节时，其页面移入第一章。`),
            ]),
            error.value ? h('div', { class: 'cw-alert error', role: 'alert' }, error.value) : null,
            h('footer', { class: 'cw-modal-footer' }, [
              h('button', { type: 'button', class: 'cw-button', disabled: busy.value, onClick: () => emit('close') }, [h('span', '取消')]),
              h('button', {
                type: 'button',
                class: 'cw-button primary',
                disabled: busy.value || missing.value > 0 || !drafts.value.length || drafts.value.some((d: any) => !d.title.trim() || !d.page_ids.length),
                onClick: save,
              }, [
                h(AppIcon, { icon: IconCheck, size: 18 }),
                h('span', busy.value ? '正在保存…' : '保存章节编排'),
              ]),
            ]),
          ]),
      });
  },
});

const FieldSchemaDialog = defineComponent({
  props: { document: { type: Object, default: () => ({}) } },
  emits: ['close', 'saved'],
  setup(props, { emit }) {
    const schema = ref(JSON.parse(JSON.stringify((props.document as any)?.extraction_schema || [])));
    const busy = ref(false);
    const error = ref('');
    function update(index: number, patch: any): void {
      schema.value = schema.value.map((field: any, i: number) => (i === index ? { ...field, ...patch } : field));
    }
    async function submit(event: Event): Promise<void> {
      event.preventDefault();
      error.value = '';
      const keys = new Set<string>();
      for (const field of schema.value) {
        if (!/^[a-zA-Z][a-zA-Z0-9_]{0,63}$/.test(field.key)) {
          error.value = '字段 key 以字母开头，仅使用字母、数字、下划线，最长64字符。';
          return;
        }
        if (keys.has(field.key)) {
          error.value = `字段 key 重复：${field.key}`;
          return;
        }
        keys.add(field.key);
        if (!field.label.trim()) {
          error.value = '每个字段都需要中文显示名称。';
          return;
        }
      }
      busy.value = true;
      try {
        await request('/documents/' + encodeURIComponent((props.document as any).id), {
          extraction_schema: schema.value.map((field: any) => ({ ...field, key: field.key.trim(), label: field.label.trim(), description: field.description.trim() })),
          expected_schema_revision: (props.document as any)?.schema_revision || 0,
        });
        emit('saved');
      } catch (e) {
        error.value = e instanceof Error ? e.message : String(e);
      } finally {
        busy.value = false;
      }
    }
    return () =>
      h(CwDialog, { title: '自定义字段提取模板', subtitle: '说明每个字段的名称、类型和含义；没有原文证据的字段必须保留 null。', wide: true, onClose: () => emit('close') }, {
        default: () =>
          h('form', { onSubmit: submit }, [
            h('div', { class: 'cw-modal-body' }, [
              h('div', { class: 'cw-schema-toolbar' }, [
                h('span', `模板第 ${(props.document as any)?.schema_revision || 0} 版 · ${schema.value.length} 个字段`),
                h('button', { type: 'button', class: 'cw-button', disabled: busy.value || schema.value.length >= 30, onClick: () => { schema.value = [...schema.value, { key: '', label: '', type: 'text', description: '' }]; } }, [
                  h(AppIcon, { icon: IconPlus, size: 18 }),
                  h('span', '增加字段'),
                ]),
                h('button', { type: 'button', class: 'cw-button', disabled: busy.value, onClick: () => { schema.value = JSON.parse(JSON.stringify(plantFieldTemplate)); } }, [
                  h(AppIcon, { icon: IconFlask, size: 18 }),
                  h('span', '采用常用植物模板'),
                ]),
              ]),
              h('div', { class: 'cw-schema-fields' }, schema.value.map((field: any, index: number) =>
                h('section', { class: 'cw-schema-field', key: index }, [
                  h('header', [
                    h('strong', `字段 ${index + 1}`),
                    h('button', { type: 'button', class: 'cw-text-button', disabled: busy.value, onClick: () => { schema.value = schema.value.filter((_: any, i: number) => i !== index); } }, [h(AppIcon, { icon: IconTrash, size: 14 }), '删除']),
                  ]),
                  h('div', { class: 'cw-schema-row' }, [
                    h('label', { class: 'cw-field' }, [h('span', 'key'), h('input', { value: field.key, maxLength: 64, required: true, disabled: busy.value, placeholder: '例如 leaf_shape', onInput: (e: Event) => update(index, { key: (e.target as HTMLInputElement).value }) })]),
                    h('label', { class: 'cw-field' }, [h('span', '显示名称'), h('input', { value: field.label, maxLength: 100, required: true, disabled: busy.value, placeholder: '例如 叶形', onInput: (e: Event) => update(index, { label: (e.target as HTMLInputElement).value }) })]),
                    h('label', { class: 'cw-field' }, [
                      h('span', '类型'),
                      h('select', { value: field.type, disabled: busy.value, onChange: (e: Event) => update(index, { type: (e.target as HTMLSelectElement).value }) }, [
                        h('option', { value: 'text' }, '文本'),
                        h('option', { value: 'number' }, '数值'),
                        h('option', { value: 'boolean' }, '是 / 否'),
                      ]),
                    ]),
                  ]),
                  h('label', { class: 'cw-field' }, [
                    h('span', '字段含义与提取规则'),
                    h('textarea', { value: field.description, rows: 2, maxLength: 1000, disabled: busy.value, placeholder: '说明要提取什么、单位和限制，不要求模型补齐缺失信息。', onInput: (e: Event) => update(index, { description: (e.target as HTMLTextAreaElement).value }) }),
                  ]),
                ]),
              )),
              !schema.value.length ? h('p', { class: 'cw-quiet' }, '当前没有字段。可以增加字段，或采用常用植物模板。') : null,
              h('div', { class: 'cw-alert' }, '保存模板后，下次模型整理会使用新模板。已生成产物和历史保留当时的模板，修改模板不会自动重写已有字段值。'),
              error.value ? h('div', { class: 'cw-alert error', role: 'alert' }, error.value) : null,
            ]),
            h('footer', { class: 'cw-modal-footer' }, [
              h('button', { type: 'button', class: 'cw-button', disabled: busy.value, onClick: () => emit('close') }, [h('span', '取消')]),
              h('button', { type: 'submit', class: 'cw-button primary', disabled: busy.value }, [h(AppIcon, { icon: IconCheck, size: 18 }), h('span', busy.value ? '正在保存…' : '保存字段模板')]),
            ]),
          ]),
      });
  },
});

const BatchReviewBar = defineComponent({
  props: {
    batchId: { type: String, default: '' },
    chapter: { type: Object, default: () => ({}) },
    dirty: { type: Boolean, default: false },
    processing: { type: Boolean, default: false },
    sftReviewed: { type: Boolean, default: false },
  },
  emits: ['changed'],
  setup(props, { emit }) {
    const detail = ref<any>(null);
    const message = ref('');
    const busy = ref(false);
    const error = ref('');
    const open = ref(false);

    async function load(): Promise<void> {
      try {
        detail.value = await platformRequest('/api/platform/batches/' + props.batchId);
        error.value = '';
      } catch (e) {
        error.value = e instanceof Error ? e.message : String(e);
      }
    }
    onMounted(load);
    watch(() => [props.batchId, props.chapter?.id, props.chapter?.revision], () => {
      load();
      message.value = '';
    });

    const batch = computed(() => detail.value?.batch || detail.value);
    const samples = computed(() => detail.value?.samples || batch.value?.samples || []);
    const sample = computed(() => samples.value.find((s: any) => s.chapter_id === props.chapter?.id));
    const expected = computed(() => sample.value?.expected_revision ?? sample.value?.revision);
    const stale = computed(() => sample.value?.stale || expected.value !== props.chapter?.revision);
    const reviewed = computed(() => (sample.value?.outcome ? { outcome: sample.value.outcome, message: sample.value.message } : null));
    const disabled = computed(() => busy.value || props.dirty || props.processing || !sample.value || stale.value);

    async function record(outcome: string): Promise<void> {
      busy.value = true;
      error.value = '';
      try {
        await platformRequest('/api/platform/batches/' + props.batchId + '/reviews', {
          chapter_id: props.chapter?.id,
          expected_revision: expected.value,
          outcome,
          message: message.value,
        });
        await load();
        emit('changed');
        setToast(outcome === 'pass' ? '本章抽检通过，已记录复核人和版本。' : '问题已记录到当前批次。');
      } catch (e: any) {
        if (e.status === 409) await load();
        error.value = e instanceof Error ? e.message : String(e);
      } finally {
        busy.value = false;
      }
    }

    return () =>
      h('section', { class: 'ph-batch-review' }, [
        h('header', [
          h(AppIcon, { icon: IconShieldCheck, size: 20 }),
          h('strong', `批次抽检 · ${String(batch.value?.name || '载入中').replace(/[（(](?:真实论文|章节管线合成示例|合成演示|测试文献)[）)]/g, '').trim()}`),
          h('button', { type: 'button', class: 'button teal-button push-right', 'aria-expanded': open.value, onClick: () => { open.value = !open.value; } }, open.value ? '收起抽检记录' : reviewed.value ? '查看抽检结论' : '记录本章抽检'),
          h('button', { type: 'button', class: 'text-button', onClick: () => go('batches?batch=' + props.batchId) }, [h(AppIcon, { icon: IconArrowLeft, size: 15 }), '返回批次']),
        ]),
        open.value
          ? [
              error.value ? h('div', { class: 'ph-error' }, error.value) : null,
              sample.value
                ? [
                    h('p', `${stale.value ? '本章内容已变化，原抽样版本已失效。请返回批次重新抽样，保留已有问题记录。' : `本次抽样版本 r${expected.value} · 对照 PDF 核查正文、解读、字段和图表。`}${props.dirty ? ' 请先保存右侧修改。' : ''}${reviewed.value ? ` 已记录：${reviewed.value.outcome === 'pass' ? '通过' : '发现问题'}。` : ''}`),
                    h('textarea', {
                      'aria-label': '本次抽检说明',
                      value: message.value,
                      maxLength: 4000,
                      placeholder: '记录发现的问题，例如叶片宽度单位有误、图号引用不一致。通过时可填写核查说明。',
                      onInput: (e: Event) => { message.value = (e.target as HTMLTextAreaElement).value; },
                    }),
                    h('div', { class: 'inline' }, [
                      h('button', { type: 'button', class: 'button', disabled: disabled.value || !message.value.trim(), onClick: () => record('issue') }, [h('span', '记录问题，待修正')]),
                      h('button', {
                        type: 'button',
                        class: 'button teal-button',
                        disabled: disabled.value || reviewed.value?.outcome === 'pass' || ((props.chapter as any)?.result?.sft_samples?.length > 0 && !props.sftReviewed),
                        onClick: () => record('pass'),
                      }, [h('span', '本章抽检通过')]),
                      h('span', { class: 'ph-subtitle' }, '操作人、批次和版本自动留痕'),
                    ]),
                    (props.chapter as any)?.result?.sft_samples?.length > 0 && !props.sftReviewed
                      ? h('p', '请在下方“核对图表、问答与来源”中核对问答候选后，再提交通过。')
                      : null,
                  ]
                : h('p', '当前章节不在本次随机样本中。可查看或修订内容；抽检结论请在本批已抽中的章节中提交。'),
            ]
          : null,
      ]);
  },
});

// ---------- 主组件状态与逻辑 ----------
const batchId = ref(readHash().get('batch') || '');
const documentRef = ref(readHash().get('document') || '');
const requestedPage = ref(readHash().get('page') || '');
const chapterRef = ref(readHash().get('chapter') || '');

const state = ref(blank);
const detail = ref<any>(null);
const documentId = ref(documentRef.value);
const chapterId = ref('');
const pageId = ref('');
const editor = ref(editorFrom(null));
const loading = ref(true);
const error = ref('');
const busy = ref('');
const dialog = ref<any>(null);
const sourceTab = ref('pdf');
const pdfPage = ref(1);
const instructions = ref('');
const resultTab = ref('sft');
const checksOpen = ref(false);
const sftReviewed = ref(false);
const success = ref<any>(null);
const productTab = ref('markdown');
const markdownMode = ref('preview');
const pendingHandoff = ref<any>(null);

let alive = true;
let busyLock = false;
let refreshSequence = 0;
let guardAction: ((saved?: any) => void) | null = null;

const notify = (message: string) => setToast(message);
const isAdmin = computed(() => props.user?.role === 'admin');

async function refresh(preferred?: string): Promise<any> {
  const requestId = ++refreshSequence;
  try {
    const next = await request('/state');
    if (!alive || requestId !== refreshSequence) return null;
    const normalized = { ...blank, ...next, model: { ...blank.model, ...next.model } };
    state.value = normalized;
    const candidate = preferred === undefined ? documentRef.value : preferred;
    const id = normalized.documents.some((doc: any) => doc.id === candidate) ? candidate : '';
    documentRef.value = id;
    documentId.value = id;
    if (!id) {
      detail.value = null;
      chapterId.value = '';
      error.value = candidate ? '这篇文献不存在，请返回文献管理重新选择。' : '';
      loading.value = false;
      return null;
    }
    const fresh = await request('/documents/' + encodeURIComponent(id));
    if (!alive || requestId !== refreshSequence || documentRef.value !== id) return null;
    detail.value = fresh;
    const target = requestedPage.value ? fresh.pages?.find((page: any) => page.id === requestedPage.value || String(page.number) === requestedPage.value) : null;
    const targetChapter = target ? fresh.chapters?.find((ch: any) => ch.page_ids?.includes(target.id)) : null;
    const idChapter = targetChapter?.id || (fresh.chapters?.some((ch: any) => ch.id === chapterRef.value) ? chapterRef.value : fresh.chapters?.[0]?.id || '');
    chapterRef.value = idChapter;
    chapterId.value = idChapter;
    if (target && targetChapter) {
      pageId.value = target.id;
      requestedPage.value = '';
    }
    error.value = '';
    loading.value = false;
    return fresh;
  } catch (failure: any) {
    if (alive && requestId === refreshSequence) {
      error.value = failure instanceof Error ? failure.message : String(failure);
      loading.value = false;
    }
    return null;
  }
}

const document = computed(() => detail.value?.document);
const pages = computed(() => detail.value?.pages || []);
const chapters = computed(() => detail.value?.chapters || []);
const pdfs = computed(() => detail.value?.pdfs || []);
const contexts = computed(() => document.value?.library_contexts || (document.value?.library_context ? [document.value.library_context] : []));
const libraryContext = computed(
  () =>
    contexts.value.find((context: any) => context.record_id === readHash().get('record') && (!readHash().get('source') || context.source_id === readHash().get('source'))) || contexts.value[0],
);
const libraryRoute = computed(() =>
  libraryContext.value ? 'library?' + new URLSearchParams({ source: libraryContext.value.source_id, record: libraryContext.value.record_id }).toString() : 'library',
);
const chapter = computed(() => chapters.value.find((ch: any) => ch.id === chapterId.value) || chapters.value[0]);
const result = computed(() => chapter.value?.result || {});
const members = computed(() => (chapter.value ? pages.value.filter((page: any) => chapter.value.page_ids?.includes(page.id)) : pages.value));
const page = computed(() => members.value.find((p: any) => p.id === pageId.value) || (document.value?.is_demo ? members.value.find((p: any) => p.images?.length) : null) || members.value[0]);
const selectedPdf = computed(() => pdfs.value.find((pdf: any) => pdf.id === document.value?.selected_pdf_id) || detail.value?.selected_pdf || pdfs.value[0]);
const offset = computed(() => Number(document.value?.pdf_page_offset || 0));
const mappedPage = computed(() => (page.value ? Number(page.value.number) + offset.value : 1));
const images = computed(() => pages.value.flatMap((p: any) => p.images || []));
const currentJobs = computed(() =>
  state.value.jobs
    .filter((job: any) => job.document_id === documentId.value)
    .sort((a: any, b: any) => String(b.created_at || '').localeCompare(String(a.created_at || ''))),
);
const currentJob = computed(() => currentJobs.value.find((job: any) => ['queued', 'processing'].includes(job.status)));
const processing = computed(() => !!currentJob.value);
const polling = computed(() => state.value.jobs.some((job: any) => ['queued', 'processing'].includes(job.status)));
const sourceCorpus = computed(() => chapter.value?.corpus || result.value.corpus || '');
const revision = computed(() => chapter.value?.revision ?? 0);
const sourceSignature = computed(() => draftSignature(editorFrom(chapter.value)));
const dirty = computed(() => !!chapter.value && editor.value.chapterId === chapter.value.id && draftSignature(editor.value) !== editor.value.baseline);
const sft = computed(() => result.value.sft_samples || []);
const reviewedCount = computed(() => chapters.value.filter((ch: any) => ch.status === 'approved').length);
const materializedCount = computed(() => document.value?.materialized_count || 0);
const validationIssues = computed(() => draftProblems(editor.value, result.value.extraction_schema || [], members.value));
const noPurpose = computed(() => document.value?.license === 'unknown' || (document.value?.license === 'research_only' && document.value?.purpose === 'training'));
const needsSftReview = computed(() => !dirty.value && chapter.value?.status === 'review' && sft.value.length > 0 && !sftReviewed.value);
const nextChapter = computed(() => chapters.value[chapters.value.findIndex((ch: any) => ch.id === chapter.value?.id) + 1]);
const handoffFailed = computed(() => !!pendingHandoff.value && !!chapter.value && pendingHandoff.value.chapterId === chapter.value.id && pendingHandoff.value.revision === revision.value && !dirty.value);
const handoffReady = computed(() => !!success.value && !!chapter.value && success.value.chapterId === chapter.value.id && success.value.revision === revision.value && !dirty.value);
const outputPurpose = computed(() =>
  document.value?.purpose === 'kb' || document.value?.license === 'research_only'
    ? '知识库'
    : document.value?.purpose === 'training'
      ? '训练数据'
      : '训练数据或知识库',
);
const step = computed(() => (!document.value ? 1 : chapter.value?.status === 'approved' && !dirty.value ? 3 : 2));

const dialogImport = computed(() => dialog.value === 'import');
const dialogSchema = computed(() => dialog.value === 'schema');
const dialogModel = computed(() => dialog.value === 'model');
const dialogMetadata = computed(() => dialog.value === 'metadata');
const dialogChapters = computed(() => dialog.value === 'chapters');
const dialogPdfUpload = computed(() => dialog.value === 'pdfUpload');
const dialogMapping = computed(() => dialog.value === 'mapping');
const dialogUnsaved = computed(() => dialog.value === 'unsaved');
const imageDialog = computed(() => typeof dialog.value === 'object' && !!(dialog.value as any)?.image);

function pageLabel(values: any[] | undefined): string {
  if (!Array.isArray(values) || !values.length) return '来源页未标注';
  return '第 ' + values.map((value: any) => pages.value.find((p: any) => p.id === value)?.number || value).join('、') + ' 页';
}

function guard(action: () => void): void {
  if (dirty.value) {
    guardAction = action;
    dialog.value = 'unsaved';
  } else {
    action();
  }
}

function changeChapter(id: string): void {
  guard(() => {
    chapterRef.value = id;
    chapterId.value = id;
    pageId.value = '';
    success.value = null;
  });
}

function acceptEditor(ch: any): void {
  if (ch) editor.value = editorFrom(ch);
}

function draftBody(): any {
  return { chapter_id: chapter.value.id, expected_revision: editor.value.revision, ...draftValues(editor.value) };
}

async function operation(type: string, fn: () => Promise<any>): Promise<any> {
  if (busyLock) return null;
  busyLock = true;
  busy.value = type;
  error.value = '';
  try {
    return await fn();
  } catch (failure: any) {
    if (failure.status === 409) await refresh();
    error.value = failure instanceof Error ? failure.message : String(failure);
    notify(failure instanceof Error ? failure.message : String(failure));
    return null;
  } finally {
    busyLock = false;
    busy.value = '';
  }
}

async function saveRevision(): Promise<any> {
  if (!dirty.value) return chapter.value;
  return operation('save', async () => {
    const outcome = await request('/revise', draftBody());
    const saved = outcome.chapter;
    acceptEditor(saved);
    success.value = null;
    pendingHandoff.value = null;
    await refresh();
    await refreshWorkspace();
    notify('图文、解读和字段修改已保存为新的待确认版本；原问答候选已移除。');
    return saved;
  });
}

async function leaveDraft(save: boolean): Promise<void> {
  const action = guardAction;
  let saved: any;
  if (save) {
    saved = await saveRevision();
    if (!saved) return;
  } else {
    acceptEditor(chapter.value);
    saved = chapter.value;
  }
  guardAction = null;
  dialog.value = null;
  action?.(saved);
}

async function run(mode: string, reorganize = false): Promise<void> {
  const visibleRevision = editor.value.chapterId === chapter.value?.id ? editor.value.revision : chapter.value?.revision;
  guard((saved?: any) =>
    operation('run', async () => {
      await request('/run', {
        document_id: document.value.id,
        chapter_ids: [chapter.value.id],
        mode,
        expected_revision: saved?.revision ?? visibleRevision,
        expected_schema_revision: document.value.schema_revision || 0,
        ...(reorganize ? { instructions: instructions.value.trim() } : {}),
      });
      if (reorganize) instructions.value = '';
      await refresh();
      success.value = null;
      pendingHandoff.value = null;
      notify(mode === 'demo' ? '合成演示整理已开始。' : 'AI整理已开始，结果会自动更新。');
    }),
  );
}

async function confirmUse(): Promise<void> {
  if (document.value?.license === 'unknown' || (document.value?.license === 'research_only' && document.value?.purpose === 'training')) {
    dialog.value = 'metadata';
    return;
  }
  await operation('confirm', async () => {
    let latest = { ...chapter.value, revision: editor.value.revision };
    let approved = latest.status === 'approved';
    let stage = 'save';
    success.value = null;
    try {
      if (dirty.value) {
        const saved = await request('/revise', draftBody());
        latest = saved.chapter;
        approved = false;
        acceptEditor(latest);
      }
      stage = 'review';
      if (!approved) {
        const outcome = await request('/review', {
          chapter_id: latest.id,
          action: 'approve',
          expected_revision: latest.revision,
          corpus: latest.corpus || latest.result?.corpus,
        });
        latest = { ...latest, ...outcome, id: latest.id };
        approved = true;
        acceptEditor(latest);
      }
      stage = 'materialize';
      const materialized = await request('/materialize', { document_id: document.value.id, chapter_id: latest.id, expected_revision: latest.revision });
      if (!materialized.assets?.length) throw Error('本章未生成可用产出，请检查文献用途与服务日志。');
      await refresh();
      await refreshWorkspace();
      pendingHandoff.value = null;
      success.value = { chapterId: chapter.value.id, revision: latest.revision, ...materialized };
      notify('本章已确认并准备好产出。');
      return materialized;
    } catch (failure: any) {
      await refresh();
      if (approved && stage === 'materialize') {
        pendingHandoff.value = { chapterId: latest.id, revision: latest.revision };
        const message = Error('已确认，送入产出未完成，请重试。' + (failure instanceof Error ? failure.message : String(failure)));
        (message as any).status = failure.status;
        throw message;
      }
      throw failure;
    }
  });
}

async function reject(): Promise<void> {
  const visibleRevision = editor.value.revision;
  guard((saved?: any) =>
    operation('reject', async () => {
      await request('/review', { chapter_id: chapter.value.id, action: 'reject', expected_revision: saved?.revision ?? visibleRevision });
      await refresh();
      await refreshWorkspace();
      notify('本章已标记暂不采用。');
    }),
  );
}

function locatePage(number: number): void {
  const target = pages.value.find((p: any) => p.number === Number(number));
  if (!target) {
    notify('引用页不在当前文献中。');
    return;
  }
  const owner = chapters.value.find((ch: any) => ch.page_ids?.includes(target.id));
  const action = () => {
    if (owner && owner.id !== chapter.value?.id) {
      chapterRef.value = owner.id;
      chapterId.value = owner.id;
    }
    pageId.value = target.id;
    pdfPage.value = Number(target.number) + offset.value;
    sourceTab.value = 'pdf';
  };
  if (owner && owner.id !== chapter.value?.id) guard(action);
  else action();
}

function openImage(image: any): void {
  dialog.value = { image, page: pages.value.find((p: any) => p.images?.some((img: any) => img.id === image.id)) };
}

function openFigure(figure: any): void {
  const image = images.value.find((img: any) => img.id === figure.image_id);
  dialog.value = { image, page: pages.value.find((p: any) => p.images?.some((img: any) => img.id === figure.image_id)) };
}

async function choosePdf(id: string): Promise<void> {
  if (!id) return;
  await operation('pdf', async () => {
    await request('/documents/' + encodeURIComponent(document.value.id), { selected_pdf_id: id });
    await refresh();
  });
}

async function loadFreshChapter(): Promise<void> {
  const fresh = await refresh();
  acceptEditor(fresh?.chapters?.find((ch: any) => ch.id === chapter.value?.id));
}

async function onImported(outcome: any): Promise<void> {
  chapterRef.value = '';
  await refresh(outcome.document?.id || outcome.documents?.[0]?.id);
  sourceTab.value = 'pdf';
  dialog.value = null;
  notify('文献已导入，可以开始对照整理。');
}

async function onSchemaSaved(): Promise<void> {
  await refresh();
  dialog.value = null;
  notify('字段模板已保存。下次模型整理使用新模板，已有结果保留原模板快照。');
}

async function onModelSaved(): Promise<void> {
  await refresh();
  dialog.value = null;
  notify('AI服务设置已保存。');
}

async function onMetadataSaved(): Promise<void> {
  await refresh();
  await refreshWorkspace();
  dialog.value = null;
  notify('文献用途已保存。');
}

async function onChaptersSaved(): Promise<void> {
  const fresh = await refresh();
  acceptEditor(fresh?.chapters?.find((ch: any) => ch.id === chapterRef.value));
  await refreshWorkspace();
  dialog.value = null;
  notify('章节已调整，请重新整理受影响章节。');
}

async function onPdfSaved(): Promise<void> {
  await refresh();
  sourceTab.value = 'pdf';
  dialog.value = null;
  notify('原始PDF已保存，可逐页对照。');
}

async function onMappingSaved(): Promise<void> {
  await refresh();
  dialog.value = null;
  notify('页码对应已保存，不会修改原始OCR内容。');
}

// 编辑器与章节同步：章节/版本变化时检测冲突
watch(
  () => [chapter.value?.id, revision.value, sourceSignature.value],
  () => {
    if (!chapter.value) return;
    const incoming = editorFrom(chapter.value);
    if (editor.value.chapterId !== chapter.value.id) {
      editor.value = incoming;
      return;
    }
    if (editor.value.revision === revision.value && editor.value.baseline === incoming.baseline) return;
    if (draftSignature(editor.value) !== editor.value.baseline) editor.value = { ...editor.value, conflict: true };
    else editor.value = incoming;
  },
);

watch(
  () => [chapter.value?.id, revision.value],
  () => {
    sftReviewed.value = false;
    checksOpen.value = false;
  },
);

watch(
  () => chapter.value?.id,
  () => {
    instructions.value = '';
    success.value = null;
    pendingHandoff.value = null;
  },
);

watch(
  () => [page.value?.id, selectedPdf.value?.id, offset.value],
  () => {
    if (page.value) pdfPage.value = mappedPage.value;
  },
);

let pollTimer: number | undefined;
watch(polling, (poll) => {
  if (pollTimer) {
    clearInterval(pollTimer);
    pollTimer = undefined;
  }
  if (poll) pollTimer = window.setInterval(() => void refresh(), 2000);
});

function beforeUnload(event: BeforeUnloadEvent): void {
  event.preventDefault();
  event.returnValue = '';
}
watch(dirty, (value) => {
  window.dispatchEvent(new CustomEvent('chapter-unsaved', { detail: { dirty: value } }));
  if (value) window.addEventListener('beforeunload', beforeUnload);
  else window.removeEventListener('beforeunload', beforeUnload);
});

watch(
  () => workspace.routeHash,
  () => {
    if (routeOf(workspace.routeHash) !== 'chapters') return;
    const params = readHash(workspace.routeHash);
    const doc = params.get('document') || '';
    if (doc && doc !== documentRef.value) {
      documentRef.value = doc;
      chapterRef.value = params.get('chapter') || '';
      requestedPage.value = params.get('page') || '';
      detail.value = null;
      loading.value = true;
      void refresh(doc);
    }
  },
);

onMounted(() => {
  alive = true;
  void refresh();
});
onUnmounted(() => {
  alive = false;
  refreshSequence += 1;
  if (pollTimer) clearInterval(pollTimer);
  window.removeEventListener('beforeunload', beforeUnload);
  window.dispatchEvent(new CustomEvent('chapter-unsaved', { detail: { dirty: false } }));
});
</script>

<template>
  <div class="cw-root cw-simple">
    <div class="ph-section-banner">
      <div class="page-heading">
        <div>
          <h1>文献整理</h1>
          <p>对照原件修订，抽检结论归入文献批次。</p>
        </div>
        <div class="heading-actions">
          <button type="button" class="pp-button" @click="go(libraryRoute)">
            <AppIcon :icon="IconChevronLeft" :size="18" /><span>返回文献列表</span>
          </button>
        </div>
      </div>
    </div>

    <div v-if="document" class="cw-document-bar">
      <AppIcon :icon="IconFileText" :size="23" />
      <strong class="cw-document-title">{{ documentTitle(document.title || document.name) }}</strong>
      <span class="cw-document-count">{{ pages.length }} 页 · {{ chapters.length }} 章</span>
      <button type="button" class="cw-text-button" @click="dialog = 'metadata'">用途设置<AppIcon :icon="IconChevronRight" :size="14" /></button>
    </div>

    <SourceMetadata v-if="document?.source_metadata" :metadata="document.source_metadata" />

    <div class="cw-simple-steps" aria-label="整理步骤">
      <template v-for="([number, label], index) in [[1, '导入文献'], [2, '对照整理'], [3, '批次抽检']]" :key="number">
        <span :class="number === step ? 'active' : number < step ? 'done' : ''">
          <b><AppIcon v-if="number < step" :icon="IconCheck" :size="13" /><template v-else>{{ number }}</template></b>
          {{ label }}
        </span>
        <AppIcon v-if="index < 2" :icon="IconChevronRight" :size="14" />
      </template>
      <p>{{ dirty ? '右侧有未保存修改，请先保存，再记录抽检结论。' : processing ? 'AI正在整理，完成后可对照原文核查。' : batchId ? '核对抽中的章节，记录通过或问题，然后返回批次。' : '检查章节产物。进入处理与抽检，将文献归入批次后生成数据包。' }}</p>
    </div>

    <div v-if="error" class="cw-alert error" role="alert">
      <AppIcon :icon="IconAlertTriangle" /><span>{{ error }}</span>
      <button type="button" @click="refresh">重新载入</button>
    </div>

    <div v-if="loading" class="cw-empty">
      <AppIcon :icon="IconLoader2" :size="28" class="cw-spinner" /><strong>正在准备原文与整理结果…</strong>
    </div>
    <div v-else-if="!document" class="cw-welcome">
      <AppIcon :icon="IconFolder" :size="38" />
      <h2>先选择数据源和文献</h2>
      <p>进入物种或数据条目后，选择已有文献，也可以导入新的文献资料。</p>
      <div class="cw-actions">
        <button type="button" class="cw-button primary" @click="go('library')"><AppIcon :icon="IconFolder" :size="18" /><span>返回文献管理</span></button>
      </div>
    </div>
    <template v-else>
      <BatchReviewBar
        v-if="batchId && chapter"
        :batch-id="batchId"
        :chapter="chapter"
        :dirty="dirty"
        :processing="processing"
        :sft-reviewed="sftReviewed"
        @changed="refreshWorkspace"
      />

      <div class="cw-chapter-bar">
        <label for="cw-chapter-select">当前章节</label>
        <select id="cw-chapter-select" :value="chapter?.id || ''" aria-label="当前章节" @change="changeChapter(($event.target as HTMLSelectElement).value)">
          <option v-for="(ch, i) in chapters" :key="ch.id" :value="ch.id">{{ i + 1 }}. {{ ch.title }} · {{ ch.page_ids?.length || 0 }} 页{{ ch.status === 'approved' ? ' · 已确认' : '' }}</option>
        </select>
        <button type="button" class="cw-text-button" :disabled="processing" @click="guard(() => (dialog = 'chapters'))">
          <AppIcon :icon="IconListDetails" :size="15" />调整章节
        </button>
        <div class="cw-actions">
          <button
            type="button"
            class="cw-button purple"
            :disabled="!!busy || processing || !state.model.configured || !chapter"
            @click="run('model')"
          >
            <AppIcon :icon="IconArrowRight" :size="18" /><span>{{ sourceCorpus ? '重新整理这一章' : '整理这一章' }}</span>
          </button>
          <button v-if="!state.model.configured && isAdmin" type="button" class="cw-text-button" @click="dialog = 'model'">配置AI服务</button>
        </div>
      </div>

      <div v-if="processing" class="cw-job-strip" role="status">
        <AppIcon :icon="IconLoader2" class="cw-spinner" />
        <span><strong>{{ currentJob.mode === 'demo' ? '合成演示' : 'AI' }}正在整理</strong><small>{{ currentJob.completed || 0 }} / {{ currentJob.chapter_count || 1 }} 章完成 · 自动更新</small></span>
        <progress :value="currentJob.completed || 0" :max="currentJob.chapter_count || 1" />
      </div>

      <div class="cw-compare-grid">
        <section class="cw-source-panel">
          <header class="cw-core-panel-heading">
            <h2>原始文献</h2>
            <div class="cw-tabs">
              <button type="button" :class="sourceTab === 'pdf' ? 'active' : ''" @click="sourceTab = 'pdf'">PDF原件</button>
              <button type="button" :class="sourceTab === 'ocr' ? 'active' : ''" @click="sourceTab = 'ocr'">OCR文字</button>
            </div>
          </header>
          <div class="cw-source-controls">
            <label>
              <span>来源页</span>
              <select :value="page?.id || ''" aria-label="定位当前章节的原始页" @change="pageId = ($event.target as HTMLSelectElement).value">
                <option v-for="p in members" :key="p.id" :value="p.id">第 {{ p.number }} 页{{ p.images?.length ? ` · ${p.images.length} 图` : '' }}</option>
              </select>
            </label>
            <template v-if="sourceTab === 'pdf'">
              <select aria-label="选择原始PDF" :value="selectedPdf?.id || ''" @change="choosePdf(($event.target as HTMLSelectElement).value)">
                <option value="" disabled>尚未添加PDF</option>
                <option v-for="pdf in pdfs" :key="pdf.id" :value="pdf.id">{{ pdf.name }}</option>
              </select>
              <button type="button" class="cw-text-button" @click="dialog = 'pdfUpload'"><AppIcon :icon="IconUpload" :size="14" />{{ pdfs.length ? '添加PDF' : '补传PDF' }}</button>
            </template>
          </div>

          <template v-if="sourceTab === 'pdf'">
            <template v-if="selectedPdf?.url">
              <div class="cw-pdf-mapping">
                <span>OCR第 {{ page?.number || '—' }} 页 → PDF第 {{ mappedPage }} 页</span>
                <button v-if="pdfPage !== mappedPage" type="button" @click="pdfPage = mappedPage">回到来源页</button>
                <button type="button" @click="dialog = 'mapping'">校正页码对应</button>
              </div>
              <div class="cw-pdf-host">
                <PdfViewer :url="selectedPdf.url" :page-number="pdfPage" @page-change="pdfPage = $event" />
              </div>
            </template>
            <div v-else class="cw-missing-pdf">
              <AppIcon :icon="IconFileText" :size="34" />
              <h3>添加原始PDF，逐页对照</h3>
              <p>当前已保存OCR文字，但尚未关联PDF原件。可补传文献PDF，也可先查看OCR文字。</p>
              <button type="button" class="cw-button primary" @click="dialog = 'pdfUpload'"><AppIcon :icon="IconUpload" :size="18" /><span>添加原始PDF</span></button>
              <button type="button" class="cw-text-button" @click="sourceTab = 'ocr'">先看OCR文字</button>
            </div>
          </template>
          <div v-else-if="page" class="cw-ocr-view">
            <div class="cw-page-kicker">原始OCR结果 · 第 {{ page.number }} 页</div>
            <div class="cw-ocr-text">
              <PageMarkdown :text="page.text" :images="page.images || []" :pdfs="pdfs" @page-link="locatePage" @image="openImage" />
            </div>
            <div class="cw-image-heading"><strong>本页图片</strong><span>{{ page.images?.length || 0 }} 张</span></div>
            <div v-if="page.images?.length" class="cw-image-grid">
              <figure v-for="image in page.images" :key="image.id" class="cw-image-card">
                <button type="button" :aria-label="`查看 ${image.caption || image.path}`" @click="dialog = { image, page }">
                  <img :src="image.url" :alt="image.caption || image.path" />
                </button>
                <figcaption>{{ image.caption || image.path?.split('/').pop() || '原始图片' }}</figcaption>
              </figure>
            </div>
            <p v-else class="cw-quiet">本页没有关联图片。</p>
            <div v-if="page.unresolved_images?.length > 0" class="cw-alert warning">有 {{ page.unresolved_images.length }} 个图片引用未匹配到文件，请核对原始路径。</div>
          </div>
          <div v-else class="cw-empty">本章尚未关联原始页，请调整章节。</div>
        </section>

        <section class="cw-result-panel">
          <header class="cw-core-panel-heading">
            <h2>章节产物</h2>
            <span v-if="chapter" class="cw-badge" :class="chapter.status">{{ chapter.status === 'approved' ? '已确认' : chapter.status === 'review' ? '待确认' : statusNames[chapter.status] }}</span>
          </header>
          <div v-if="chapter?.error" class="cw-alert error">{{ chapter.error }}</div>

          <template v-if="sourceCorpus">
            <div class="cw-result-intro">
              <span>{{ result.human_edited ? '已保存修改' : '章节整理结果' }}</span>
              <small>第 {{ revision }} 版{{ dirty ? ' · 有未保存修改' : '' }}</small>
            </div>

            <div v-if="editor.conflict" class="cw-alert error">
              已有新的版本。你的草稿仍保留，请复制草稿或载入新版本后再保存。
              <button type="button" :disabled="!!busy" @click="loadFreshChapter">放弃草稿并载入新版本</button>
            </div>

            <ChapterProducts
              :editor="editor"
              :result="result"
              :document="document"
              :members="members"
              :pdfs="pdfs"
              :product-tab="productTab"
              :markdown-mode="markdownMode"
              :on-schema="isAdmin"
              :disabled="processing || !!busy"
              @update:productTab="productTab = $event"
              @update:markdownMode="markdownMode = $event"
              @update:editor="editor = $event"
              @locate="locatePage"
              @image="openImage"
              @schema="dialog = 'schema'"
            />

            <div class="cw-editor-bottom">
              <span>{{ dirty ? '图文、解读或字段有未保存修改' : '同一版本保留两种产物' }}</span>
              <button type="button" class="cw-button" :disabled="!dirty || !!busy || processing || editor.conflict || !!validationIssues.length" @click="saveRevision">
                <AppIcon :icon="IconCheck" :size="18" /><span>{{ busy === 'save' ? '正在保存…' : '保存修改' }}</span>
              </button>
            </div>

            <div v-if="validationIssues.length > 0" class="cw-alert warning">
              {{ validationIssues.slice(0, 2).join(' ') }}{{ validationIssues.length > 2 ? ` 另有 ${validationIssues.length - 2} 项需补充。` : null }}
            </div>

            <div v-if="dirty && sft.length > 0" class="cw-alert warning">产物已修改。保存后，原有 {{ sft.length }} 组问答候选将移除；本次确认使用当前图文、解读和字段。</div>

            <div v-if="!dirty && chapter.status === 'review' && sft.length > 0" class="cw-question-prompt">
              <AppIcon :icon="IconShieldCheck" :size="16" />
              <span>本章还有 {{ sft.length }} 组问答候选，一起使用前需要核对。</span>
              <button type="button" @click="checksOpen = true; resultTab = 'sft'">核对问答</button>
            </div>

            <div v-if="noPurpose" class="cw-alert warning">使用前需确认文献的授权范围。<button type="button" @click="dialog = 'metadata'">设置用途</button></div>

            <div class="cw-primary-actions">
              <span class="cw-quiet">产出以批次抽检结论为准</span>
              <button type="button" class="cw-button" @click="guard(() => go(batchId ? 'batches?batch=' + batchId : 'batches?document=' + document.id))">
                <AppIcon :icon="IconShieldCheck" :size="18" /><span>{{ batchId ? '返回处理与抽检' : '加入文献批次' }}</span>
              </button>
            </div>

            <div v-if="handoffReady" class="cw-success">
              <AppIcon :icon="IconCheck" :size="17" />
              <span>已准备好，可用于{{ outputPurpose }}。</span>
              <div>
                <button v-if="nextChapter" type="button" @click="changeChapter(nextChapter.id)">下一章<AppIcon :icon="IconChevronRight" :size="14" /></button>
                <button type="button" @click="guard(() => go('outputs'))">查看产出<AppIcon :icon="IconArrowRight" :size="14" /></button>
              </div>
            </div>
          </template>
          <div v-else class="cw-result-blank">
            <AppIcon :icon="IconDatabase" :size="32" />
            <h3>{{ processing ? 'AI正在整理这一章…' : '这一章还没有整理结果' }}</h3>
            <p>点击上方“整理这一章”。结果会保留原文来源，你可以直接修改或告诉AI重新组织。</p>
            <button v-if="!state.model.configured && isAdmin" type="button" class="cw-button" @click="dialog = 'model'">
              <AppIcon :icon="IconSettings" :size="18" /><span>配置AI服务</span>
            </button>
          </div>

          <details class="cw-instructions">
            <summary>告诉AI怎么改<span>用一句话重新组织内容</span></summary>
            <div>
              <textarea v-model="instructions" maxlength="4000" rows="3" placeholder="例如：保留原始数据和单位，把这一章按“方法、结果、局限”整理；不要添加原文没有的结论。" aria-label="给AI的重新整理指令" />
              <div class="cw-model-line">
                <span>{{ state.model.configured ? `${state.model.model} · ${state.model.endpoint}` : '尚未配置AI服务' }}</span>
                <button v-if="isAdmin" type="button" @click="dialog = 'model'">{{ state.model.configured ? '更换服务' : '配置服务' }}</button>
              </div>
              <button type="button" class="cw-button purple" :disabled="!state.model.configured || !instructions.trim() || processing || !!busy || !chapter" @click="run('model', true)">
                <AppIcon :icon="IconArrowRight" :size="18" /><span>按指令重新整理</span>
              </button>
              <small>会生成新的待确认版本。若有未保存修改，会先让你选择保存或放弃。</small>
            </div>
          </details>
        </section>
      </div>

      <details v-if="sourceCorpus" class="cw-checks-drawer" :open="checksOpen" @toggle="checksOpen = ($event.target as HTMLDetailsElement).open">
        <summary>
          <AppIcon :icon="IconShieldCheck" :size="17" />核对图表、问答与来源<span>{{ sft.length }} 组问答 · {{ (result.figures?.length || 0) + (result.tables?.length || 0) }} 项图表</span>
        </summary>
        <div class="cw-tabs">
          <button type="button" :class="resultTab === 'sft' ? 'active' : ''" @click="resultTab = 'sft'">问答候选 ({{ sft.length }})</button>
          <button type="button" :class="resultTab === 'figures' ? 'active' : ''" @click="resultTab = 'figures'">图表</button>
          <button type="button" :class="resultTab === 'provenance' ? 'active' : ''" @click="resultTab = 'provenance'">来源与提醒</button>
        </div>
        <div v-if="resultTab === 'sft'" class="cw-sft-results">
          <template v-if="sft.length">
            <article v-for="(sample, index) in sft" :key="index" class="cw-sft-card">
              <header><span class="cw-badge review">问答候选 {{ index + 1 }}</span><small>{{ pageLabel(sample.source_pages) }}</small></header>
              <div><span>问题</span><p>{{ sample.question }}</p></div>
              <div><span>答案</span><p>{{ sample.answer }}</p></div>
            </article>
            <label v-if="['review', 'approved'].includes(chapter.status) && !dirty" class="cw-checkbox cw-sft-check">
              <input type="checkbox" :checked="sftReviewed" @change="sftReviewed = ($event.target as HTMLInputElement).checked" />我已对照原文核对这些问答和来源页
            </label>
          </template>
          <p v-else class="cw-quiet">本章没有问答候选。可单独使用已确认正文。</p>
        </div>
        <div v-else-if="resultTab === 'figures'" class="cw-structured-results">
          <article v-for="(figure, index) in result.figures" :key="index" class="cw-figure">
            <button v-if="images.find(img => img.id === figure.image_id)" type="button" @click="openFigure(figure)">
              <img :src="images.find(img => img.id === figure.image_id)!.url" :alt="figure.caption || images.find(img => img.id === figure.image_id)!.path" />
            </button>
            <div>
              <h3>图 {{ index + 1 }} · {{ figure.caption || '说明待确认' }}</h3>
              <p>{{ figure.interpretation || '未给出可核验解释，请对照原图。' }}</p>
              <small>{{ pageLabel(figure.source_pages) }}</small>
            </div>
          </article>
          <article v-for="(table, index) in result.tables" :key="index" class="cw-table-card">
            <h3>表 {{ index + 1 }} · {{ table.title || '原文表格' }}</h3>
            <PageMarkdown :text="table.markdown || '未提供表格正文。'" />
            <small>{{ pageLabel(table.source_pages) }}</small>
          </article>
          <p v-if="!result.figures?.length && !result.tables?.length" class="cw-quiet">本章暂无结构化图表结果；左侧保留实际原件和图片。</p>
        </div>
        <div v-else class="cw-provenance">
          <p><strong>本章来源：</strong>{{ pageLabel(members.map(p => p.number)) }} · {{ document.title || document.name }}</p>
          <div v-for="(fact, index) in result.facts" :key="index" class="cw-fact"><p>{{ fact.text }}</p><small>{{ pageLabel(fact.source_pages) }}</small></div>
          <template v-if="result.warnings?.length">
            <div v-for="(warning, index) in result.warnings" :key="index" class="cw-alert warning">{{ typeof warning === 'string' ? warning : warning.message || JSON.stringify(warning) }}</div>
          </template>
          <p v-else class="cw-quiet">未列出异常，不代表内容已经核验。请以原始PDF和实验记录为准。</p>
        </div>
      </details>

      <details class="cw-more">
        <summary>更多设置与阶段文件<span>模型、用途、记录与下载</span></summary>
        <div class="cw-more-actions">
          <button v-if="isAdmin" type="button" class="cw-button" @click="dialog = 'model'"><AppIcon :icon="IconSettings" :size="18" /><span>AI服务设置</span></button>
          <button type="button" class="cw-button" @click="dialog = 'metadata'"><AppIcon :icon="IconShieldCheck" :size="18" /><span>文献用途</span></button>
          <button type="button" class="cw-button" @click="guard(() => (dialog = 'chapters'))"><AppIcon :icon="IconListDetails" :size="18" /><span>章节编排</span></button>
          <button type="button" class="cw-button" @click="dialog = 'pdfUpload'"><AppIcon :icon="IconUpload" :size="18" /><span>添加原始PDF</span></button>
          <button v-if="isAdmin" type="button" class="cw-button" @click="dialog = 'schema'"><AppIcon :icon="IconTable" :size="18" /><span>字段模板</span></button>
        </div>
        <div class="cw-artifact-links">
          <a :href="`/api/chapters/download/${encodeURIComponent(document.id)}?stage=pages`" download><AppIcon :icon="IconDownload" :size="15" />下载逐页资料</a>
          <a :href="`/api/chapters/download/${encodeURIComponent(document.id)}?stage=chapters`" download><AppIcon :icon="IconDownload" :size="15" />下载章节编排</a>
          <a :class="{ disabled: !reviewedCount }" :href="reviewedCount ? `/api/chapters/download/${encodeURIComponent(document.id)}?stage=corpus` : undefined" download>
            <AppIcon :icon="IconDownload" :size="15" />下载已确认语料
          </a>
          <span>{{ reviewedCount }} 章已确认 · {{ materializedCount }} 份已可用于产出</span>
        </div>
        <div class="cw-job-history">
          <h3>AI整理记录</h3>
          <template v-if="currentJobs.length">
            <div class="cw-job-table-wrap">
              <table class="cw-job-table">
                <thead>
                  <tr><th>方式</th><th>状态</th><th>完成章节</th><th>时间</th><th>结果</th></tr>
                </thead>
                <tbody>
                  <tr v-for="job in currentJobs" :key="job.id">
                    <td>{{ job.mode === 'model' ? 'AI整理' : '规则整理' }}</td>
                    <td>{{ job.status === 'completed' ? '已完成' : statusNames[job.status] }}</td>
                    <td>{{ job.completed || 0 }}/{{ job.chapter_count || 0 }}</td>
                    <td>{{ dateLabel(job.created_at) }}</td>
                    <td>{{ job.error || (job.status === 'completed' ? '已保存' : '处理中') }}</td>
                  </tr>
                </tbody>
              </table>
            </div>
          </template>
          <p v-else class="cw-quiet">尚无整理记录。</p>
        </div>
        <RevisionHistory v-if="chapter" :chapter-id="chapter.id" :revision="revision" :status="chapter.status" @locate="locatePage" />
      </details>
    </template>

    <ChapterImportDialog v-if="dialogImport" :context="libraryContext" @close="dialog = null" @imported="onImported" />
    <FieldSchemaDialog v-if="dialogSchema && isAdmin && document" :document="document" @close="dialog = null" @saved="onSchemaSaved" />
    <ModelDialog v-if="dialogModel && isAdmin" :model="state.model" @close="dialog = null" @saved="onModelSaved" />
    <MetadataDialog v-if="dialogMetadata && document" :document="document" @close="dialog = null" @saved="onMetadataSaved" />
    <ChapterEditor v-if="dialogChapters && detail" :detail="detail" @close="dialog = null" @saved="onChaptersSaved" />
    <PdfUploadDialog v-if="dialogPdfUpload && document" :document="document" @close="dialog = null" @saved="onPdfSaved" />
    <PageMappingDialog v-if="dialogMapping && document" :document="document" :source-page="page?.number || 1" @close="dialog = null" @saved="onMappingSaved" />

    <CwDialog v-if="dialogUnsaved" title="还有未保存的修改" subtitle="保存后会生成新的待确认版本；你也可以放弃草稿继续。" @close="guardAction = null; dialog = null">
      <p class="cw-guard-message">{{ sft.length ? '保存产物修改会移除当前版本的问答候选。' : '原始文献和已保存版本都会保留。' }}</p>
      <div v-if="error" class="cw-alert error">{{ error }}</div>
      <div class="cw-modal-footer">
        <button type="button" class="cw-button" :disabled="!!busy" @click="guardAction = null; dialog = null">继续编辑</button>
        <button type="button" class="cw-button" :disabled="!!busy" @click="leaveDraft(false)">放弃修改并继续</button>
        <button type="button" class="cw-button primary" :disabled="!!busy || editor.conflict || !!validationIssues.length" @click="leaveDraft(true)">保存并继续</button>
      </div>
    </CwDialog>

    <CwDialog v-if="imageDialog" :title="dialog.image.caption || '原始页面图片'" :subtitle="`第 ${dialog.page?.number || '—'} 页 · ${dialog.image.path || ''}`" wide @close="dialog = null">
      <div class="cw-image-preview">
        <img :src="dialog.image.url" :alt="dialog.image.caption || dialog.image.path" />
      </div>
      <div class="cw-modal-footer">
        <a class="cw-button primary" :href="dialog.image.url" target="_blank" rel="noreferrer">打开原始图片</a>
        <button type="button" class="cw-button" @click="dialog = null">关闭</button>
      </div>
    </CwDialog>
  </div>
</template>
