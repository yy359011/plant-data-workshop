<script setup lang="ts">
// 数据资产（对照 DataHub.jsx DataHub）
import { onMounted, ref, watch } from 'vue';
import {
  IconDatabase,
  IconFileText,
  IconUpload,
  IconLink,
  IconScan,
  IconSettings,
  IconBook2,
  IconX,
} from '@tabler/icons-vue';
import AppIcon from '../components/common/AppIcon.vue';
import AppButton from '../components/common/AppButton.vue';
import AppModal from '../components/common/AppModal.vue';
import { platformRequest, platformDelete } from '../api/client';
import { go, viewAsset, setToast, workspace } from '../stores/workspace';

// Vue 3 <script setup> 模板无法直接访问全局构造器；显式暴露给模板使用
const URLSearchParams = globalThis.URLSearchParams;

const props = defineProps<{ user: any }>();
const emit = defineEmits<{ (e: 'import'): void }>();

const displayTitle = (value: unknown) =>
  String(value || '').replace(/[（(](?:真实论文|章节管线合成示例|合成演示|测试文献)[）)]/g, '').trim();
const kindNames: Record<string, string> = {
  asset: '资料文件',
  pdf: 'PDF 原件',
  image: '文献原图',
  page: '逐页 Markdown',
  chapter: '章节语料',
};
const states: Record<string, string> = {
  original: '已归档',
  pending: '未处理',
  ready: '已解析',
  review: '待复核',
  needs_ocr: '待 OCR',
  error: '处理失败',
  approved: '已确认',
  rejected: '不采用',
  queued: '排队中',
  processing: '处理中',
  completed: '已完成',
  blocked: '待配置服务',
};

const hub = ref<any>(null);
const tasks = ref<any[]>([]);
const docs = ref<any[]>([]);
const batches = ref<any[]>([]);
const query = ref('');
const kind = ref('all');
const status = ref('all');
const batchFilter = ref('');
const error = ref('');
const busy = ref(false);
const deletingAsset = ref<any>(null);
const link = ref<any>(null);
const documentId = ref('');
const batchId = ref('');
const preview = ref<any>(null);
const limit = ref(80);
const tab = ref('data');

async function load(): Promise<void> {
  try {
    const [h, t, l, b] = await Promise.all([
      platformRequest('/api/data-hub'),
      platformRequest('/api/ocr/tasks'),
      platformRequest('/api/library/state'),
      platformRequest('/api/platform/batches'),
    ]);
    hub.value = h;
    tasks.value = t.tasks || [];
    docs.value = l.documents || [];
    batches.value = b.batches || [];
    error.value = '';
  } catch (e) {
    error.value = e instanceof Error ? e.message : String(e);
  }
}

watch(
  () => workspace.data.assets,
  () => void load(),
);
watch(tasks, (list) => {
  if (!list.some((t) => ['queued', 'processing'].includes(t.status))) return;
  const timer = setInterval(load, 2500);
  return () => clearInterval(timer);
});

onMounted(() => void load());

async function action(fn: () => Promise<unknown>, msg?: string): Promise<unknown> {
  busy.value = true;
  error.value = '';
  try {
    const d = await fn();
    await load();
    if (msg) setToast(msg);
    return d;
  } catch (e) {
    error.value = e instanceof Error ? e.message : String(e);
    return null;
  } finally {
    busy.value = false;
  }
}

function confirmDelete(r: any): void {
  deletingAsset.value = r;
}
async function doDelete(): Promise<void> {
  const r = deletingAsset.value;
  if (!r?.asset_id) return;
  deletingAsset.value = null;
  await action(
    () => platformDelete('/api/data-hub/assets/' + r.asset_id),
    `已删除：${displayTitle(r.name)}`,
  );
}

const rows = () =>
  (hub.value?.rows || []).filter(
    (r: any) =>
      (kind.value === 'all' || r.kind === kind.value) &&
      (status.value === 'all' || r.status === status.value) &&
      (!batchFilter.value || (r.batch_ids || []).includes(batchFilter.value)) &&
      `${displayTitle(r.name)} ${r.preview || ''} ${r.document_title || ''}`
        .toLowerCase()
        .includes(query.value.toLowerCase()),
  );

function ocr(row: any): void {
  void action(() => {
    const body: Record<string, unknown> = {
      asset_id: row.asset_id,
      ...(row.record_id ? { record_id: row.record_id } : {}),
      ...(row.source_id ? { source_id: row.source_id } : {}),
      ...(row.batch_id
        ? { batch_id: row.batch_id }
        : row.batch_ids?.length === 1
          ? { batch_id: row.batch_ids[0] }
          : {}),
    };
    return platformRequest('/api/ocr/tasks', body);
  }).then((out) => {
    if (!out) return;
    tab.value = 'ocr';
    setToast(
      out.task?.status === 'blocked'
        ? 'OCR 任务已登记，请管理员配置服务后重试。'
        : 'OCR 任务已提交。',
    );
  });
}

function ingest(row: any): void {
  void action(() => platformRequest('/api/data-hub/ingest', { asset_id: row.asset_id }), 'Markdown 已转入文献管理，保留原始资料关联。').then(
    (out) => {
      if (out?.document?.id) go('chapters?document=' + out.document.id);
    },
  );
}

function openLink(row: any): void {
  link.value = row;
  documentId.value = row.document_id || row.document_ids?.[0] || '';
  batchId.value = row.batch_ids?.[0] || '';
}

function open(row: any): void {
  if (row.kind === 'image') {
    preview.value = row;
    return;
  }
  if (row.document_id) {
    const params = new URLSearchParams({ document: row.document_id });
    if (row.page) params.set('page', row.page);
    if (row.chapter_id) params.set('chapter', row.chapter_id);
    go('chapters?' + params.toString());
  } else if (row.asset_id) {
    void viewAsset(row.asset_id);
  } else {
    preview.value = row;
  }
}

function saveLink(): void {
  void action(
    () =>
      platformRequest('/api/data-hub/link', {
        asset_id: link.value.asset_id,
        document_id: documentId.value || undefined,
        batch_id: batchId.value || undefined,
      }),
    '数据关联已保存',
  ).then(() => (link.value = null));
}
</script>

<template>
  <div class="ph-page">
    <div class="ph-section-banner">
      <div class="page-heading">
        <div>
          <h1>数据资产</h1>
          <p>原始资料、OCR 成果与加工语料统一归集，关联文献和处理批次。</p>
        </div>
        <div class="heading-actions">
          <button class="button primary" @click="emit('import')"><AppIcon :icon="IconUpload" :size="18" />导入资料</button>
        </div>
      </div>
    </div>

    <div class="ph-tabs">
      <button :class="{ active: tab === 'data' }" @click="tab = 'data'">全部数据</button>
      <button :class="{ active: tab === 'ocr' }" @click="tab = 'ocr'">OCR 任务 <small>{{ tasks.length }}</small></button>
      <button v-if="user.role === 'admin'" @click="go('services')">配置 OCR 服务<AppIcon :icon="IconSettings" :size="14" /></button>
    </div>

    <div v-if="error" class="ph-error" role="alert">
      {{ error }}
      <button class="text-button" @click="load">重新加载</button>
    </div>

    <template v-if="tab === 'data'">
      <section class="card">
        <div class="ph-hub-filters">
          <input v-model="query" aria-label="筛选数据资产" placeholder="筛选名称或内容片段" />
          <select v-model="kind" aria-label="资产层级">
            <option value="all">全部数据层级</option>
            <option v-for="(v, k) in kindNames" :key="k" :value="k">{{ v }}</option>
          </select>
          <select v-model="status" aria-label="资产处理状态">
            <option value="all">全部处理状态</option>
            <option v-for="(v, k) in states" :key="k" :value="k">{{ v }}</option>
          </select>
          <select v-model="batchFilter" aria-label="资产所属批次">
            <option value="">全部批次</option>
            <option v-for="b in batches" :key="b.id" :value="b.id">{{ displayTitle(b.name) }}</option>
          </select>
        </div>

        <div v-if="!hub" class="ph-empty">正在归集资产…</div>
        <template v-else-if="rows().length">
          <div class="table-wrap">
            <table class="ph-hub-table">
              <thead>
                <tr><th>数据名称</th><th>层级</th><th>状态</th><th>关联文献 / 批次</th><th>操作</th></tr>
              </thead>
              <tbody>
                <tr v-for="r in rows().slice(0, limit)" :key="r.id">
                  <td>
                    <button class="text-button ph-row-title" @click="open(r)">
                      {{
                        r.provenance?.source_document_title
                          ? `${displayTitle(r.provenance.source_document_title)} · 第 ${(r.provenance.source_pages || []).join('、')} 页${r.provenance.artifact_type === 'sft' ? '问答样本' : '语料'}`
                          : displayTitle(r.name)
                      }}
                    </button>
                    <small class="muted">{{ String(r.format || r.kind).toUpperCase() }}{{ r.page ? ` · 第 ${r.page} 页` : '' }}{{ r.revision != null ? ` · r${r.revision}` : '' }}</small>
                  </td>
                  <td>
                    <span :class="['ph-stage', { green: r.kind === 'chapter' }]">
                      {{ r.kind === 'asset' && r.provenance?.source_document_id ? '加工产物' : kindNames[r.kind] || r.kind }}
                    </span>
                  </td>
                  <td><span :class="['badge', r.status]">{{ states[r.status] || r.status }}</span></td>
                  <td>
                    <template v-for="id in (r.document_ids || [r.document_id]).filter(Boolean)" :key="'d' + id">
                      <button class="text-button block tiny" @click="go('chapters?document=' + id)">文献：{{ displayTitle(docs.find((d) => d.id === id)?.title || r.document_title || '关联文献') }}</button>
                    </template>
                    <template v-for="id in (r.batch_ids || [])" :key="'b' + id">
                      <button class="text-button block tiny" @click="go('batches?batch=' + id)">批次：{{ displayTitle(batches.find((b) => b.id === id)?.name || '查看所属批次') }}</button>
                    </template>
                    <span v-if="!r.document_id && !r.document_ids?.length && !r.batch_ids?.length" class="muted tiny">尚未关联</span>
                  </td>
                  <td>
                    <div class="ph-actions">
                      <button class="text-button" @click="open(r)">查看</button>
                      <button v-if="r.asset_id" class="text-button" @click="openLink(r)">关联</button>
                      <button
                        v-if="r.asset_id && ['pdf', 'png', 'jpg', 'jpeg', 'webp'].includes(String(r.format).toLowerCase().replace('.', ''))"
                        class="text-button"
                        :disabled="busy"
                        @click="ocr(r)"
                      >
                        OCR
                      </button>
                      <button
                        v-if="r.asset_id && ['md', 'txt'].includes(String(r.format).toLowerCase()) && !r.document_ids?.length && !r.document_id"
                        class="text-button"
                        :disabled="busy"
                        @click="ingest(r)"
                      >
                        转入文献
                      </button>
                      <button
                        v-if="r.asset_id && ['pending', 'error'].includes(r.status) && !['pdf', 'png', 'jpg', 'jpeg'].includes(String(r.format).toLowerCase())"
                        class="text-button"
                        :disabled="busy"
                        @click="action(() => platformRequest('/api/jobs', { asset_ids: [r.asset_id] }), '资料已解析')"
                      >
                        解析
                      </button>
                      <button
                        v-if="r.asset_id"
                        class="text-button danger"
                        :disabled="busy"
                        @click="confirmDelete(r)"
                      >
                        删除
                      </button>
                    </div>
                  </td>
                </tr>
              </tbody>
            </table>
          </div>
          <div class="ph-pad ph-actions">
            <span class="muted tiny">共 {{ rows().length }} 项数据</span>
            <button v-if="rows().length > limit" class="button" @click="limit += 80">显示更多</button>
          </div>
        </template>
        <div v-else class="ph-empty">
          <strong>暂无匹配数据</strong>
          <p>调整筛选条件，或导入 PDF、Markdown 和科研数据。</p>
        </div>
      </section>
      <p class="ph-slim-note">原件与加工结果分别保存。逐页 Markdown 可以继续组装章节，章节语料按文献批次完成抽检后生成数据包。</p>
    </template>

    <template v-else>
      <section class="card ph-pad">
        <h2>OCR 处理记录</h2>
        <p class="muted">任务从原始 PDF 或图片识别文字，成功后保存逐页 Markdown 并关联文献。</p>
        <div v-if="tasks.length" class="table-wrap">
          <table>
            <thead>
              <tr><th>来源资料</th><th>状态</th><th>结果 / 说明</th><th>操作</th></tr>
            </thead>
            <tbody>
              <tr v-for="t in tasks" :key="t.id">
                <td>
                  {{ hub?.rows?.find((r) => r.asset_id === t.asset_id)?.name || t.asset_name || t.asset_id }}
                  <small class="block muted">{{ new Date(t.created_at).toLocaleString('zh-CN') }}</small>
                </td>
                <td><span :class="['badge', t.status]">{{ states[t.status] || t.status }}</span></td>
                <td class="ph-audit-detail">{{ t.error || (t.document_id ? '逐页成果已归档' : '等待识别结果') }}</td>
                <td>
                  <button v-if="t.document_id" class="text-button" @click="go('chapters?document=' + t.document_id)">打开文献</button>
                  <button v-else-if="['error', 'blocked'].includes(t.status)" class="text-button" :disabled="busy" @click="ocr(t)">重试</button>
                </td>
              </tr>
            </tbody>
          </table>
        </div>
        <div v-else class="ph-empty">从"全部数据"中的 PDF 或图片启动 OCR。</div>
      </section>
    </template>

    <AppModal v-if="link" title="关联数据资产" @close="link = null">
      <p>{{ link.name }}</p>
      <form class="ph-form-grid" @submit.prevent="saveLink">
        <label>
          所属文献
          <select v-model="documentId">
            <option value="">选择文献</option>
            <option v-for="d in docs" :key="d.id" :value="d.id">{{ displayTitle(d.title || d.name) }}</option>
          </select>
        </label>
        <label>
          所属批次
          <select v-model="batchId">
            <option value="">选择批次</option>
            <option v-for="b in batches" :key="b.id" :value="b.id">{{ displayTitle(b.name) }}</option>
          </select>
        </label>
        <button class="button primary" :disabled="busy || (!documentId && !batchId)">保存关联</button>
      </form>
    </AppModal>

    <AppModal v-if="preview" :title="displayTitle(preview.name)" wide @close="preview = null">
      <img
        v-if="preview.kind === 'image'"
        class="ph-image-preview"
        :src="preview.url"
        :alt="preview.name"
      />
      <div class="ph-actions" v-if="preview.kind === 'image'">
        <a class="button" :href="preview.url" target="_blank" rel="noreferrer">查看原图</a>
        <button class="button" @click="go('chapters?' + new URLSearchParams({ document: preview.document_id, ...(preview.page ? { page: preview.page } : {}) }).toString())">返回文献原页</button>
      </div>
      <pre v-else>{{ preview.preview }}</pre>
    </AppModal>

    <AppModal v-if="deletingAsset" title="删除资料" wide :busy="busy" @close="deletingAsset = null">
      <p>确定要删除资料 <strong>{{ displayTitle(deletingAsset.name) }}</strong> 吗？</p>
      <p class="muted tiny">该资料关联的 Markdown、章节语料、检索样本会一并清除；原始文件也会从磁盘移除。此操作无法撤销。</p>
      <template #footer>
        <div style="display:flex; gap:10px; justify-content:flex-end">
          <button class="button" :disabled="busy" @click="deletingAsset = null">取消</button>
          <button class="button primary danger" :disabled="busy" @click="doDelete">
            {{ busy ? '删除中…' : '确认删除' }}
          </button>
        </div>
      </template>
    </AppModal>
  </div>
</template>
