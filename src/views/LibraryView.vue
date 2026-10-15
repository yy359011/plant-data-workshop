<script setup lang="ts">
// 文献管理（对照 DocumentLibrary.jsx DocumentLibrary）
import { computed, onMounted, ref, watch } from 'vue';
import {
  IconDatabase,
  IconFolder,
  IconLeaf,
  IconFileText,
  IconPlus,
  IconSearch,
  IconChevronRight,
  IconRefresh,
  IconLink,
  IconX,
  IconAlertTriangle,
  IconSettings,
  IconExternalLink,
  IconUpload,
  IconPlugConnected,
  IconBook2,
  IconCheck,
} from '@tabler/icons-vue';
import AppIcon from '../components/common/AppIcon.vue';
import AppModal from '../components/common/AppModal.vue';
import { platformRequest } from '../api/client';
import { go, workspace, routeOf, setToast } from '../stores/workspace';

// Vue 3 <script setup> 模板无法直接访问全局构造器；显式暴露给模板使用
const URLSearchParams = globalThis.URLSearchParams;

const emit = defineEmits<{ (e: 'import'): void }>();

const statusNames: Record<string, string> = {
  pending: '待整理',
  ready: '已就绪',
  queued: '排队中',
  processing: '整理中',
  review: '待确认',
  approved: '已确认',
  rejected: '暂不采用',
  error: '整理失败',
  materialized: '已准备产出',
  api_pending: '待接入',
};
const purposeNames: Record<string, string> = { both: '训练数据 + 知识库', training: '训练数据', kb: '知识库' };
const licenseNames: Record<string, string> = {
  self_owned: '自有 / 已获授权',
  research_only: '仅内部知识库',
  unknown: '授权待核对',
};
const normalized = (value: unknown) => String(value || '').toLocaleLowerCase().trim();
const matches = (query: string, ...values: unknown[]) =>
  !query || values.some((value) => normalized(value).includes(normalized(query)));
const documentTitle = (document: any) =>
  String(document.title || document.name || '未命名文献').replace(/（(?:真实论文|章节管线合成示例)）\s*$/, '');
const sourceName = (source: any) =>
  ({ '真实论文源': '开放植物文献', '合成演示源': '植物研究资料' })[source?.name] || source?.name || '未命名数据源';
const safeHttpUrl = (value: unknown): string | null => {
  try {
    const url = new URL(String(value));
    return /^https?:$/.test(url.protocol) ? url.href : null;
  } catch {
    return null;
  }
};

const state = ref({ sources: [] as any[], records: [] as any[], links: [] as any[], documents: [] as any[] });
const query = ref('');
const loading = ref(true);
const error = ref('');
const dialog = ref<any>(null);

const route = computed(() => {
  const params = new URLSearchParams(workspace.routeHash.split('?')[1] || '');
  return { source: params.get('source') || '', record: params.get('record') || '' };
});

async function refresh(): Promise<void> {
  try {
    const value = await platformRequest('/api/library/state');
    state.value = value || { sources: [], records: [], links: [], documents: [] };
    error.value = '';
    loading.value = false;
  } catch (failure) {
    error.value = failure instanceof Error ? failure.message : String(failure);
    loading.value = false;
  }
}

onMounted(refresh);

watch(
  () => [route.value.source, route.value.record],
  () => {
    query.value = '';
    dialog.value = null;
    void refresh();
  },
);

const source = computed(() =>
  route.value.source ? state.value.sources.find((item) => item.id === route.value.source) : null,
);
const record = computed(() =>
  route.value.record
    ? state.value.records.find((item) => item.id === route.value.record && item.source_id === route.value.source)
    : null,
);
const invalid = computed(
  () => !loading.value && ((route.value.record && !route.value.source) || (route.value.source && !source.value) || (route.value.record && !record.value)),
);
const level = computed(() => (route.value.record ? 3 : route.value.source ? 2 : 1));

function sourceRecords(id: string): any[] {
  return state.value.records.filter((item) => item.source_id === id);
}
function recordDocuments(id: string): any[] {
  const ids = new Set(state.value.links.filter((link) => link.record_id === id).map((link) => link.document_id));
  return state.value.documents.filter((document) => ids.has(document.id));
}
function countSourceDocuments(id: string): number {
  const recordIds = new Set(sourceRecords(id).map((item) => item.id));
  return new Set(state.value.links.filter((link) => recordIds.has(link.record_id)).map((link) => link.document_id)).size;
}

const documents = computed(() => (record.value ? recordDocuments(record.value.id) : []));
const sourceItems = computed(() =>
  state.value.sources.filter((item) =>
    matches(query.value, sourceName(item), item.description, item.kind === 'api' ? 'API 数据源' : '本地资料'),
  ),
);
const recordItems = computed(() =>
  source.value ? sourceRecords(source.value.id).filter((item) => matches(query.value, item.name, item.scientific_name, item.summary)) : [],
);
const documentItems = computed(() =>
  documents.value.filter((item) => {
    const metadata = item.source_metadata || {};
    return matches(query.value, documentTitle(item), metadata.original_title, metadata.doi, Array.isArray(metadata.authors) ? metadata.authors.join(' ') : String(metadata.authors || ''));
  }),
);

const shown = computed(() => (level.value === 1 ? sourceItems.value.length : level.value === 2 ? recordItems.value.length : documentItems.value.length));
const total = computed(() => (level.value === 1 ? state.value.sources.length : level.value === 2 ? sourceRecords(source.value?.id).length : documents.value.length));
const heading = computed(() => (level.value === 1 ? '文献管理' : level.value === 2 ? sourceName(source.value) : record.value?.name || '文献列表'));
const subtitle = computed(() =>
  level.value === 1
    ? '先选择数据源，再按物种或数据条目查看文献。'
    : level.value === 2
      ? '选择物种或数据条目，查看它关联的文献。'
      : '选择文献，进入原文对照与章节整理。',
);
const primaryLabel = computed(() => (level.value === 1 ? '新建数据源' : level.value === 2 ? '新建物种 / 条目' : '导入文献'));

function primary(): void {
  if (level.value === 1) dialog.value = { type: 'source' };
  else if (level.value === 2) dialog.value = { type: 'record' };
  else emit('import');
}

function libraryRoute(sid = '', rid = ''): string {
  const params = new URLSearchParams();
  if (sid) params.set('source', sid);
  if (rid) params.set('record', rid);
  return 'library' + (params.size ? '?' + params.toString() : '');
}

const kindOptions = [
  { value: 'local', label: '本地资料', text: '导入已有文献与资料', icon: IconFolder },
  { value: 'api', label: 'API 数据源', text: '先登记，待接口信息齐备后接入', icon: IconPlugConnected },
];

const sourceDialog = ref({ name: '', description: '', kind: 'local', endpoint: '', busy: false, error: '' });
const recordDialog = ref({ name: '', scientificName: '', summary: '', busy: false, error: '' });
const linkDialog = ref({ query: '', selected: '', busy: false, error: '' });
const linkedIds = computed(() => new Set(documents.value.map((document) => document.id)));

watch(dialog, (d) => {
  if (!d) return;
  if (d.type === 'source') {
    sourceDialog.value = { name: d.item?.name || '', description: d.item?.description || '', kind: d.item?.kind || 'local', endpoint: d.item?.endpoint || '', busy: false, error: '' };
  } else if (d.type === 'record') {
    recordDialog.value = { name: d.item?.name || '', scientificName: d.item?.scientific_name || '', summary: d.item?.summary || '', busy: false, error: '' };
  } else if (d.type === 'link') {
    linkDialog.value = { query: '', selected: '', busy: false, error: '' };
  }
});

async function submitSource(): Promise<void> {
  sourceDialog.value.busy = true;
  sourceDialog.value.error = '';
  const editing = !!dialog.value.item;
  try {
    const value = await platformRequest(
      '/api/library/sources' + (editing ? '/' + encodeURIComponent(dialog.value.item.id) : ''),
      {
        name: sourceDialog.value.name.trim(),
        description: sourceDialog.value.description.trim(),
        kind: sourceDialog.value.kind,
        endpoint: sourceDialog.value.kind === 'api' ? sourceDialog.value.endpoint.trim() : '',
      },
    );
    await refresh();
    dialog.value = null;
    setToast(editing ? '数据源信息已保存。' : '数据源已建立，请继续创建物种或条目。');
    if (!editing) go(libraryRoute(value.source.id));
  } catch (failure) {
    sourceDialog.value.error = failure instanceof Error ? failure.message : String(failure);
  } finally {
    sourceDialog.value.busy = false;
  }
}

async function submitRecord(): Promise<void> {
  recordDialog.value.busy = true;
  recordDialog.value.error = '';
  const editing = !!dialog.value.item;
  try {
    const value = await platformRequest(
      '/api/library/records' + (editing ? '/' + encodeURIComponent(dialog.value.item.id) : ''),
      {
        source_id: source.value.id,
        name: recordDialog.value.name.trim(),
        scientific_name: recordDialog.value.scientificName.trim(),
        summary: recordDialog.value.summary.trim(),
      },
    );
    await refresh();
    dialog.value = null;
    setToast(editing ? '条目信息已保存。' : '条目已建立，可以导入或关联文献。');
    if (!editing) go(libraryRoute(source.value.id, value.record.id));
  } catch (failure) {
    recordDialog.value.error = failure instanceof Error ? failure.message : String(failure);
  } finally {
    recordDialog.value.busy = false;
  }
}

async function submitLink(): Promise<void> {
  if (!linkDialog.value.selected) return;
  linkDialog.value.busy = true;
  linkDialog.value.error = '';
  try {
    await platformRequest('/api/library/link', {
      record_id: record.value.id,
      source_id: source.value.id,
      document_id: linkDialog.value.selected,
    });
    await refresh();
    dialog.value = null;
    setToast('文献已关联到当前条目，原文与其他条目关联保留。');
  } catch (failure) {
    linkDialog.value.error = failure instanceof Error ? failure.message : String(failure);
  } finally {
    linkDialog.value.busy = false;
  }
}
</script>

<template>
  <div class="dl-root">
    <div class="ph-section-banner">
      <div class="page-heading">
        <div>
          <h1>{{ invalid ? '未找到指定资料' : heading }}</h1>
          <p>{{ subtitle }}</p>
        </div>
        <div class="heading-actions">
          <button v-if="level > 1" class="button" @click="go(level === 3 ? libraryRoute(route.source) : 'library')">
            <AppIcon :icon="IconChevronRight" :size="15" style="transform: rotate(180deg)" />返回{{ level === 3 ? '条目' : '数据源' }}
        </button>
        <button
          v-if="!invalid"
          class="button primary"
          :disabled="loading"
          @click="primary"
        >
          <AppIcon :icon="level === 3 ? IconUpload : IconPlus" :size="15" />{{ primaryLabel }}
        </button>
      </div>
      </div>
    </div>

    <div v-if="error" class="dl-notice error" role="alert">
      <AppIcon :icon="IconAlertTriangle" /><span>{{ error }}</span>
      <button @click="refresh">重试</button>
    </div>

    <div v-if="loading" class="dl-empty">
      <AppIcon :icon="IconRefresh" :size="34" class="dl-spinner" />
      <h3>正在载入文献库…</h3>
    </div>

    <template v-else-if="invalid">
      <div class="dl-content-card">
        <div class="dl-empty">
          <AppIcon :icon="IconAlertTriangle" :size="34" />
          <h3>该路径没有对应的数据源或条目</h3>
          <p>指定资料可能已移除，或条目不属于这个数据源。返回上一级重新选择。</p>
          <div class="dl-actions"><button class="dl-button" @click="go('library')">返回数据源</button></div>
        </div>
      </div>
    </template>

    <template v-else>
      <section v-if="source" class="dl-context-card">
        <div :class="['dl-context-icon', { api: source.kind === 'api' }]">
          <AppIcon :icon="record ? IconLeaf : source.kind === 'api' ? IconPlugConnected : IconFolder" :size="25" />
        </div>
        <div class="dl-context-copy">
          <h2>{{ record ? record.name : sourceName(source) }}<span v-if="source.kind === 'api'" class="dl-badge api_pending">待接入</span></h2>
          <em v-if="record?.scientific_name">{{ record.scientific_name }}</em>
          <p>{{ record ? record.summary || '此条目尚未填写说明。' : source.description || '此数据源尚未填写说明。' }}</p>
        </div>
        <div class="dl-context-meta">
          <strong>{{ record ? documents.length : sourceRecords(source.id).length }}</strong>
          <span>{{ record ? '关联文献' : '物种 / 条目' }}</span>
          <button class="dl-text-button" @click="dialog = record ? { type: 'record', item: record } : { type: 'source', item: source }">
            <AppIcon :icon="IconSettings" :size="13" />编辑信息
          </button>
        </div>
      </section>

      <div v-if="source?.kind === 'api'" class="dl-notice">
        <AppIcon :icon="IconPlugConnected" />
        <span>API 数据源已登记，尚未接入。现有条目和文献来自本地关联；接入需补充接口地址、鉴权方式和返回字段。</span>
        <button @click="dialog = { type: 'source', item: source }">查看登记</button>
      </div>

      <div class="dl-toolbar">
        <label class="dl-search">
          <AppIcon :icon="IconSearch" :size="17" />
          <input
            v-model="query"
            type="search"
            :aria-label="level === 1 ? '搜索数据源' : level === 2 ? '搜索物种或数据条目' : '搜索当前条目的文献'"
            :placeholder="level === 1 ? '搜索数据源名称或说明' : level === 2 ? '搜索名称、学名或内容' : '搜索文献名称、作者或 DOI'"
          />
          <button v-if="query" aria-label="清除搜索" @click="query = ''"><AppIcon :icon="IconX" :size="14" /></button>
        </label>
        <span class="dl-count">{{ query ? `${shown} / ${total}` : total }} {{ level === 1 ? '个数据源' : level === 2 ? '个条目' : '篇文献' }}</span>
        <button v-if="level === 3" class="dl-button" @click="dialog = { type: 'link' }"><AppIcon :icon="IconLink" />关联已有文献</button>
        <button class="dl-icon-button" aria-label="刷新文献库" @click="refresh"><AppIcon :icon="IconRefresh" :size="17" /></button>
      </div>

      <!-- 数据源层 -->
      <div v-if="level === 1" class="dl-source-grid">
        <article v-for="item in sourceItems" :key="item.id" class="dl-source-card">
          <header>
            <span :class="['dl-source-icon', { api: item.kind === 'api' }]">
              <AppIcon :icon="item.kind === 'api' ? IconPlugConnected : IconFolder" :size="25" />
            </span>
            <span :class="['dl-badge', item.kind === 'api' ? 'api_pending' : 'ready']">{{ item.kind === 'api' ? '待接入' : '本地可用' }}</span>
            <button class="dl-icon-button" :aria-label="'编辑数据源 ' + sourceName(item)" @click="dialog = { type: 'source', item }">
              <AppIcon :icon="IconSettings" :size="16" />
            </button>
          </header>
          <a class="dl-source-title" :href="'#' + libraryRoute(item.id)" @click.prevent="go(libraryRoute(item.id))">{{ sourceName(item) }}</a>
          <p>{{ item.description || (item.kind === 'api' ? '登记接口资料后，可在此组织相关物种与文献。' : '按物种或数据条目组织本地文献与原始资料。') }}</p>
          <div class="dl-source-counts">
            <span><strong>{{ item.record_count ?? sourceRecords(item.id).length }}</strong>物种 / 条目</span>
            <span><strong>{{ item.document_count ?? countSourceDocuments(item.id) }}</strong>关联文献</span>
          </div>
          <footer>
            <span>{{ item.kind === 'api' ? 'API 数据源' : '本地资料' }}</span>
            <a :href="'#' + libraryRoute(item.id)" @click.prevent="go(libraryRoute(item.id))">查看条目<AppIcon :icon="IconChevronRight" :size="14" /></a>
          </footer>
        </article>
        <div v-if="!sourceItems.length" class="dl-content-card dl-span-all">
          <div class="dl-empty">
            <AppIcon :icon="IconDatabase" :size="34" />
            <h3>{{ query ? '没有匹配的数据源' : '建立第一个数据源' }}</h3>
            <p>{{ query ? '试试其他名称，或清除搜索条件。' : '可以从本地资料开始，也可以先登记待接入的 API 数据源。' }}</p>
            <div class="dl-actions">
              <button v-if="query" class="dl-button" @click="query = ''">清除搜索</button>
              <button v-else class="dl-button primary" @click="dialog = { type: 'source' }"><AppIcon :icon="IconPlus" />新建数据源</button>
            </div>
          </div>
        </div>
      </div>

      <!-- 条目层 -->
      <div v-else-if="level === 2" class="dl-content-card">
        <div class="dl-record-header"><span>物种 / 数据条目</span><span>关联文献</span><span>操作</span></div>
        <template v-if="recordItems.length">
          <article v-for="item in recordItems" :key="item.id" class="dl-record-row">
            <div class="dl-record-main">
              <span class="dl-record-icon"><AppIcon :icon="IconLeaf" :size="20" /></span>
              <div>
                <a :href="'#' + libraryRoute(source.id, item.id)" @click.prevent="go(libraryRoute(source.id, item.id))">{{ item.name }}</a>
                <em v-if="item.scientific_name">{{ item.scientific_name }}</em>
                <p>{{ item.summary || '尚未填写条目说明' }}</p>
              </div>
            </div>
            <span class="dl-record-count"><strong>{{ item.document_count ?? recordDocuments(item.id).length }}</strong><small>篇文献</small></span>
            <div class="dl-record-actions">
              <a :href="'#' + libraryRoute(source.id, item.id)" @click.prevent="go(libraryRoute(source.id, item.id))">查看文献<AppIcon :icon="IconChevronRight" :size="14" /></a>
              <button class="dl-icon-button" :aria-label="'编辑条目 ' + item.name" @click="dialog = { type: 'record', item }"><AppIcon :icon="IconSettings" :size="15" /></button>
            </div>
          </article>
        </template>
        <div v-else class="dl-empty">
          <AppIcon :icon="IconLeaf" :size="34" />
          <h3>{{ query ? '没有匹配的物种或条目' : '这个数据源还没有条目' }}</h3>
          <p>{{ query ? '可以按中文名称、植物学名或条目说明搜索。' : '创建物种或数据条目，再将文献导入或关联到它。' }}</p>
          <div class="dl-actions">
            <button v-if="query" class="dl-button" @click="query = ''">清除搜索</button>
            <button v-else class="dl-button primary" @click="dialog = { type: 'record' }"><AppIcon :icon="IconPlus" />新建物种 / 条目</button>
          </div>
        </div>
      </div>

      <!-- 文献层 -->
      <div v-else class="dl-document-grid">
        <template v-if="documentItems.length">
          <article v-for="document in documentItems" :key="document.id" class="dl-document-card">
            <header>
              <span class="dl-document-icon"><AppIcon :icon="IconFileText" :size="24" /></span>
              <div>
                <a class="dl-document-title" :href="'#chapters?' + new URLSearchParams({ document: document.id, source: source.id, record: record.id }).toString()" @click.prevent="go('chapters?' + new URLSearchParams({ document: document.id, source: source.id, record: record.id }).toString())">{{ documentTitle(document) }}</a>
                <p v-if="(document.source_metadata?.authors || []).length || document.source_metadata?.year">
                  {{ Array.isArray(document.source_metadata?.authors) ? document.source_metadata.authors.join('、') : String(document.source_metadata?.authors || '') }}{{ document.source_metadata?.year ? ` · ${document.source_metadata.year}` : '' }}
                </p>
              </div>
              <span :class="['dl-badge', document.status]">{{ statusNames[document.status] || document.status }}</span>
            </header>
            <div class="dl-document-facts">
              <span>{{ document.page_count ?? 0 }} 页</span><span>{{ document.chapter_count ?? 0 }} 章</span><span>{{ document.image_count ?? 0 }} 张图</span>
              <span>{{ document.pdf_count ? '含 PDF 原件' : '可补传 PDF' }}</span>
            </div>
            <p v-if="document.source_metadata?.doi" class="dl-document-doi">DOI · {{ document.source_metadata.doi }}</p>
            <div class="dl-document-scope">
              <span>{{ purposeNames[document.purpose] || '用途待设置' }}</span>
              <span>{{ document.source_metadata?.license_label || licenseNames[document.license] || '授权待核对' }}</span>
            </div>
            <footer>
              <a v-if="safeHttpUrl(document.source_metadata?.url)" :href="safeHttpUrl(document.source_metadata.url)!" target="_blank" rel="noopener noreferrer">文献来源<AppIcon :icon="IconExternalLink" :size="12" /></a>
              <span v-else>本地文献资料</span>
              <a class="dl-open-document" :href="'#chapters?' + new URLSearchParams({ document: document.id, source: source.id, record: record.id }).toString()" @click.prevent="go('chapters?' + new URLSearchParams({ document: document.id, source: source.id, record: record.id }).toString())">打开文献<AppIcon :icon="IconChevronRight" :size="15" /></a>
            </footer>
          </article>
        </template>
        <div v-if="!documentItems.length" class="dl-content-card dl-span-all">
          <div class="dl-empty">
            <AppIcon :icon="IconBook2" :size="34" />
            <h3>{{ query ? '没有匹配的文献' : '这个条目还没有文献' }}</h3>
            <p>{{ query ? '试试文献名称、作者或 DOI。' : '导入此条目的文献，或关联文献库中已有的资料。' }}</p>
            <div class="dl-actions">
              <button v-if="query" class="dl-button" @click="query = ''">清除搜索</button>
              <template v-else>
                <button class="dl-button primary" @click="emit('import')"><AppIcon :icon="IconUpload" />导入文献</button>
                <button class="dl-button" @click="dialog = { type: 'link' }"><AppIcon :icon="IconLink" />关联已有文献</button>
              </template>
            </div>
          </div>
        </div>
      </div>
    </template>

    <!-- 弹窗区 -->
    <AppModal v-if="dialog?.type === 'source'" :title="dialog.item ? '编辑数据源' : '新建数据源'" @close="dialog = null">
      <p class="dl-muted-copy">先确定资料归属，再组织物种、数据条目和文献。</p>
      <form @submit.prevent="submitSource">
        <div class="dl-modal-body">
          <label class="dl-field">
            <span>数据源名称</span>
            <input v-model="sourceDialog.name" required maxlength="200" :disabled="sourceDialog.busy" placeholder="例如：植物研究资料" />
          </label>
          <fieldset class="dl-kind-picker">
            <legend>数据源类型</legend>
            <label v-for="option in kindOptions" :key="option.value" :class="{ active: sourceDialog.kind === option.value }">
              <input v-model="sourceDialog.kind" type="radio" :value="option.value" :disabled="sourceDialog.busy" />
              <AppIcon :icon="option.icon" :size="20" />
              <span><strong>{{ option.label }}</strong><small>{{ option.text }}</small></span>
            </label>
          </fieldset>
          <label class="dl-field">
            <span>资料说明</span>
            <textarea v-model="sourceDialog.description" maxlength="4000" :disabled="sourceDialog.busy" rows="3" placeholder="描述资料范围、用途或接口提供方。" />
          </label>
          <template v-if="sourceDialog.kind === 'api'">
            <label class="dl-field">
              <span>接口地址 <small>可稍后填写</small></span>
              <input v-model="sourceDialog.endpoint" type="url" :disabled="sourceDialog.busy" placeholder="https://…" autoComplete="off" />
            </label>
            <div class="dl-inline-note"><AppIcon :icon="IconPlugConnected" :size="18" /><p>当前仅保存 API 数据源登记，状态为"待接入"。没有接口地址时也可建立；此操作不会联网、同步或生成条目。</p></div>
          </template>
          <div v-if="sourceDialog.error" class="dl-form-error" role="alert">{{ sourceDialog.error }}</div>
        </div>
        <footer class="dl-modal-footer">
          <button type="button" class="dl-button" :disabled="sourceDialog.busy" @click="dialog = null">取消</button>
          <button type="submit" class="dl-button primary" :disabled="sourceDialog.busy || !sourceDialog.name.trim()">
            <AppIcon :icon="IconCheck" />{{ sourceDialog.busy ? '正在保存…' : dialog.item ? '保存信息' : '建立数据源' }}
          </button>
        </footer>
      </form>
    </AppModal>

    <AppModal v-if="dialog?.type === 'record' && source" :title="dialog.item ? '编辑物种 / 条目' : '新建物种 / 数据条目'" @close="dialog = null">
      <p class="dl-muted-copy">所属数据源：{{ sourceName(source) }}</p>
      <form @submit.prevent="submitRecord">
        <div class="dl-modal-body">
          <label class="dl-field">
            <span>名称</span>
            <input v-model="recordDialog.name" required maxlength="200" :disabled="recordDialog.busy" placeholder="例如：Pothos deleonii 或种子萌发资料" />
          </label>
          <label class="dl-field">
            <span>植物学名 <small>非物种条目可留空</small></span>
            <input v-model="recordDialog.scientificName" maxlength="300" :disabled="recordDialog.busy" placeholder="填写准确的学名" />
          </label>
          <label class="dl-field">
            <span>条目内容说明</span>
            <textarea v-model="recordDialog.summary" maxlength="4000" rows="4" :disabled="recordDialog.busy" placeholder="说明此条目涵盖的研究资料、植物性状或数据范围。" />
          </label>
          <div class="dl-inline-note"><AppIcon :icon="IconLeaf" :size="18" /><p>条目用于组织资料。建立后，可导入新文献，也可关联库中已有文献；同一篇文献可归入多个条目。</p></div>
          <div v-if="recordDialog.error" class="dl-form-error" role="alert">{{ recordDialog.error }}</div>
        </div>
        <footer class="dl-modal-footer">
          <button type="button" class="dl-button" :disabled="recordDialog.busy" @click="dialog = null">取消</button>
          <button type="submit" class="dl-button primary" :disabled="recordDialog.busy || !recordDialog.name.trim()">
            <AppIcon :icon="IconCheck" />{{ recordDialog.busy ? '正在保存…' : dialog.item ? '保存信息' : '建立条目' }}
          </button>
        </footer>
      </form>
    </AppModal>

    <AppModal v-if="dialog?.type === 'link' && record" title="关联已有文献" wide @close="dialog = null">
      <p class="dl-muted-copy">将文献归入 {{ record.name }}，保留原文件和已有的其他关联。</p>
      <form @submit.prevent="submitLink">
        <div class="dl-modal-body">
          <label class="dl-search">
            <AppIcon :icon="IconSearch" :size="16" />
            <input v-model="linkDialog.query" type="search" :disabled="linkDialog.busy" placeholder="搜索已有文献" aria-label="搜索可关联的文献" />
          </label>
          <div class="dl-link-list">
            <template v-if="state.documents.filter((document) => matches(linkDialog.query, documentTitle(document), document.source_metadata?.doi)).length">
              <label
                v-for="document in state.documents.filter((document) => matches(linkDialog.query, documentTitle(document), document.source_metadata?.doi))"
                :key="document.id"
                :class="['dl-link-option', { selected: linkDialog.selected === document.id, linked: linkedIds.has(document.id) }]"
              >
                <input v-model="linkDialog.selected" type="radio" :value="document.id" :disabled="linkDialog.busy || linkedIds.has(document.id)" />
                <AppIcon :icon="IconFileText" :size="21" />
                <span><strong>{{ documentTitle(document) }}</strong><small>{{ document.page_count || 0 }} 页 · {{ document.chapter_count || 0 }} 章</small></span>
                <span v-if="linkedIds.has(document.id)" class="dl-badge approved">已关联</span>
                <span v-else :class="['dl-badge', document.status]">{{ statusNames[document.status] || document.status }}</span>
              </label>
            </template>
            <div v-else class="dl-empty">
              <AppIcon :icon="IconBook2" :size="34" />
              <h3>{{ linkDialog.query ? '没有匹配的文献' : '库中还没有文献' }}</h3>
              <p>{{ linkDialog.query ? '修改搜索条件后重试。' : '先在条目页导入一篇文献。' }}</p>
            </div>
          </div>
          <div v-if="linkDialog.error" class="dl-form-error" role="alert">{{ linkDialog.error }}</div>
        </div>
        <footer class="dl-modal-footer">
          <button type="button" class="dl-button" :disabled="linkDialog.busy" @click="dialog = null">取消</button>
          <button type="submit" class="dl-button primary" :disabled="linkDialog.busy || !linkDialog.selected">
            <AppIcon :icon="IconLink" />{{ linkDialog.busy ? '正在关联…' : '关联到当前条目' }}
          </button>
        </footer>
      </form>
    </AppModal>
  </div>
</template>
