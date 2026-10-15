<script setup lang="ts">
// 处理与抽检（对照 BatchWorkspace.jsx BatchWorkspace）
import { computed, onMounted, ref, watch } from 'vue';
import {
  IconArrowRight,
  IconCheck,
  IconChevronLeft,
  IconChevronRight,
  IconDatabase,
  IconFileText,
  IconFlask,
  IconLayersIntersect,
  IconLoader2,
  IconPlus,
  IconSearch,
  IconShieldCheck,
  IconTable,
  IconAlertTriangle,
  IconDownload,
} from '@tabler/icons-vue';
import AppIcon from '../components/common/AppIcon.vue';
import AppModal from '../components/common/AppModal.vue';
import { platformRequest } from '../api/client';
import { go, setToast, workspace, routeOf } from '../stores/workspace';
import { readHash } from '../composables/useHashRoute';
import { cleanTitle, date as formatDate } from '../utils/format';

const props = defineProps<{ user: any }>();

const statusLabels: Record<string, string> = {
  pending: '待处理',
  created: '待处理',
  ready: '已就绪',
  queued: '排队中',
  processing: '处理中',
  running: '处理中',
  completed: '已有结果',
  review: '待抽检',
  unreviewed: '待抽检',
  reviewed: '抽检完成',
  accepted: '已验收',
  approved: '已通过',
  pass: '通过',
  issue: '有问题',
  error: '失败',
  draft: '草案',
  published: '已发布',
  historical: '历史版本',
  stale: '修订已变化',
  open: '待处理',
  resolved: '已闭环',
};

const stratumLabels: Record<string, string> = { text: '文本', table: '表格', image: '图像' };
const literatureLabels: Record<string, string> = {
  paper: '论文',
  flora: '植物志',
  specimen: '标本',
  report: '报告',
  other: '其他文献',
};
const packageTypes: Record<string, string> = {
  pretrain: '正文训练集',
  sft: '问答训练集',
  preference: '偏好训练集',
  image: '图文训练集',
  kb: '知识库',
};
const packageRoles: Record<string, string> = { admin: '仅管理员', internal: '内部成员', external: '外部成员' };

const stratumLabel = (item: any): string => {
  const parts = String(item.stratum || '').split('/');
  const literature = item.literature_type || (parts.length > 1 ? parts[0] : '');
  const content = item.content_type || parts[parts.length - 1];
  return [literatureLabels[literature], stratumLabels[content] || content].filter(Boolean).join(' · ') || '其他内容';
};

const admin = computed(() => props.user?.role === 'admin');
const worker = computed(() => admin.value || props.user?.role === 'internal');

const batchId = ref(readHash().get('batch') || '');
const requestedDocument = ref(readHash().get('document') || '');
const batches = ref<any[]>([]);
const detail = ref<any>(null);
const documents = ref<any[]>([]);
const prompts = ref<any[]>([]);
const query = ref('');
const loading = ref(true);
const error = ref('');
const busy = ref('');
const dialog = ref<any>(null);
let lock = false;

async function refresh(): Promise<any> {
  if (!worker.value) {
    loading.value = false;
    return null;
  }
  try {
    const requests = [
      platformRequest('/api/platform/batches'),
      platformRequest('/api/library/state'),
    ];
    if (admin.value) requests.push(platformRequest('/api/platform/prompts'));
    const values = await Promise.all(requests);
    const selected = batchId.value;
    const next = selected ? await platformRequest('/api/platform/batches/' + encodeURIComponent(selected)) : null;
    batches.value = values[0].batches || [];
    documents.value = values[1].documents || [];
    prompts.value = admin.value ? values[2].prompts || [] : [];
    detail.value = next;
    error.value = '';
    loading.value = false;
    return next;
  } catch (failure) {
    error.value = failure instanceof Error ? failure.message : String(failure);
    loading.value = false;
    return null;
  }
}

onMounted(refresh);

watch(
  () => workspace.routeHash,
  () => {
    if (routeOf(workspace.routeHash) !== 'batches') return;
    const id = readHash().get('batch') || '';
    batchId.value = id;
    loading.value = true;
    detail.value = null;
    dialog.value = null;
    void refresh();
  },
);

// 深链接带 document 参数（无批次）时，进入批次后弹出新建批次对话框并预选该文献
watch([loading, batchId], ([l, id]) => {
  if (!l && requestedDocument.value && !id) {
    const documentId = requestedDocument.value;
    requestedDocument.value = '';
    dialog.value = { type: 'create', documentId };
  }
});

const jobs = computed(() => detail.value?.jobs || []);
const processing = computed(() => jobs.value.some((job: any) => ['queued', 'processing', 'running'].includes(job.status)));

watch(processing, (p) => {
  if (!p) return;
  const timer = setInterval(refresh, 2000);
  return () => clearInterval(timer);
});

async function action(name: string, fn: () => Promise<unknown>): Promise<any> {
  if (lock) return null;
  lock = true;
  busy.value = name;
  error.value = '';
  try {
    const value = await fn();
    await refresh();
    return value;
  } catch (failure: any) {
    if (failure?.status === 409) await refresh();
    error.value = failure instanceof Error ? failure.message : String(failure);
    setToast(error.value);
    return null;
  } finally {
    lock = false;
    busy.value = '';
  }
}

const batch = computed(() => detail.value?.batch);
const chapters = computed(() => detail.value?.chapters || []);
const samples = computed(() => detail.value?.samples || []);
const issues = computed(() => detail.value?.issues || []);
const reviewed = computed(() => samples.value.filter((s: any) => s.outcome && !s.stale).length);
const passed = computed(() => samples.value.filter((s: any) => s.outcome === 'pass' && !s.stale).length);
const openIssues = computed(() => issues.value.filter((issue: any) => issue.status === 'open'));
const available = computed(() =>
  batches.value.filter(
    (item) => !query.value || [item.name, item.type, item.status].some((value) => String(value || '').toLowerCase().includes(query.value.toLowerCase())),
  ),
);
const sampleRate = computed(() => (reviewed.value ? Math.round((passed.value / reviewed.value) * 100) : null));

function openChapter(item: any): void {
  const params = new URLSearchParams({
    document: item.document_id,
    chapter: item.chapter_id || item.id,
    batch: batch.value.id,
  });
  go('chapters?' + params.toString());
}

async function run(promptId: string): Promise<void> {
  const value = await action('run', () =>
    platformRequest('/api/platform/batches/' + encodeURIComponent(batch.value.id) + '/run', admin.value && promptId ? { prompt_id: promptId } : {}),
  );
  if (value) {
    dialog.value = null;
    setToast('批次已提交；实际运行状态会自动更新。');
  }
}

// —— 章节状态派生（模板 badge 用）——
function sampleStatus(ch: any): string {
  const sample = samples.value.find((s: any) => s.chapter_id === (ch.chapter_id || ch.id));
  if (['queued', 'processing', 'error', 'rejected'].includes(ch.status)) return ch.status;
  if (sample?.stale) return 'historical';
  return sample?.outcome || (ch.has_result ? 'unreviewed' : 'pending');
}

// —— 五个操作弹窗的本地表单状态与提交 ——
const createForm = ref<{ name: string; selected: string[]; types: Record<string, string>; rate: number; query: string; busy: boolean; error: string }>({
  name: '',
  selected: [],
  types: {},
  rate: 0.2,
  query: '',
  busy: false,
  error: '',
});
const runForm = ref({ promptId: '' });
const reviewForm = ref({ message: '', checked: false, busy: false, error: '' });
const resolveForm = ref({ resolution: '', busy: false, error: '' });
const packageForm = ref({ name: '', type: 'pretrain', role: 'internal', chunkSize: 900, busy: false, error: '' });

const publishedPrompts = computed(() => prompts.value.filter((p) => p.status === 'published'));

const createGroups = computed(() => {
  const groups = new Map<string, any[]>();
  for (const doc of documents.value) {
    if (createForm.value.query && !cleanTitle(doc.title).toLowerCase().includes(createForm.value.query.toLowerCase())) continue;
    const type = createForm.value.types[doc.id] || 'other';
    if (!groups.has(type)) groups.set(type, []);
    groups.get(type)!.push(doc);
  }
  return [...groups];
});

watch(dialog, (d) => {
  if (!d) return;
  if (d.type === 'create') {
    createForm.value = {
      name: '',
      selected: documents.value.some((doc: any) => doc.id === d.documentId) ? [d.documentId] : [],
      types: Object.fromEntries(
        documents.value.map((doc: any) => [
          doc.id,
          Object.hasOwn(literatureLabels, doc.document_type || doc.source_metadata?.document_type)
            ? doc.document_type || doc.source_metadata?.document_type
            : 'other',
        ]),
      ),
      rate: 0.2,
      query: '',
      busy: false,
      error: '',
    };
  } else if (d.type === 'run') {
    runForm.value = { promptId: publishedPrompts.value.find((p) => p.is_default)?.id || '' };
  } else if (d.type === 'review') {
    reviewForm.value = { message: '', checked: false, busy: false, error: '' };
  } else if (d.type === 'resolve') {
    resolveForm.value = { resolution: '', busy: false, error: '' };
  } else if (d.type === 'package') {
    packageForm.value = { name: batch.value?.name || '', type: 'pretrain', role: 'internal', chunkSize: 900, busy: false, error: '' };
  }
});

async function submitCreate(): Promise<void> {
  createForm.value.busy = true;
  createForm.value.error = '';
  try {
    const value = await platformRequest('/api/platform/batches', {
      name: createForm.value.name.trim(),
      document_ids: createForm.value.selected,
      document_types: Object.fromEntries(createForm.value.selected.map((id) => [id, createForm.value.types[id] || 'other'])),
      sampling_rate: Number(createForm.value.rate),
    });
    await refresh();
    dialog.value = null;
    const id = value.batch?.id || value.id;
    if (id) go('batches?batch=' + encodeURIComponent(id));
    setToast('批次已建立。');
  } catch (failure) {
    createForm.value.error = failure instanceof Error ? failure.message : String(failure);
  } finally {
    createForm.value.busy = false;
  }
}

async function submitReview(): Promise<void> {
  reviewForm.value.busy = true;
  reviewForm.value.error = '';
  try {
    await platformRequest('/api/platform/batches/' + encodeURIComponent(batch.value.id) + '/reviews', {
      chapter_id: dialog.value.sample.chapter_id,
      expected_revision: dialog.value.sample.expected_revision ?? dialog.value.sample.revision,
      outcome: dialog.value.outcome,
      message: reviewForm.value.message.trim(),
    });
    await refresh();
    dialog.value = null;
    setToast('固定版本的抽检记录已保存。');
  } catch (failure) {
    reviewForm.value.error = failure instanceof Error ? failure.message : String(failure);
  } finally {
    reviewForm.value.busy = false;
  }
}

async function submitResolve(): Promise<void> {
  resolveForm.value.busy = true;
  resolveForm.value.error = '';
  try {
    await platformRequest('/api/platform/issues/' + encodeURIComponent(dialog.value.issue.id), {
      action: 'resolve',
      resolution: resolveForm.value.resolution.trim(),
    });
    await refresh();
    dialog.value = null;
    setToast('问题闭环说明已记录；仍需重新抽样复核。');
  } catch (failure) {
    resolveForm.value.error = failure instanceof Error ? failure.message : String(failure);
  } finally {
    resolveForm.value.busy = false;
  }
}

async function submitPackage(): Promise<void> {
  packageForm.value.busy = true;
  packageForm.value.error = '';
  try {
    const body: Record<string, unknown> = {
      batch_id: batch.value.id,
      name: packageForm.value.name.trim(),
      type: packageForm.value.type,
      minimum_role: admin.value ? packageForm.value.role : 'internal',
    };
    if (packageForm.value.type === 'kb') body.chunk_size = Number(packageForm.value.chunkSize);
    const value = await platformRequest('/api/packages', body);
    dialog.value = null;
    setToast('产出包已生成。');
    const id = value.package?.id || value.id;
    if (id) go('outputs?package=' + encodeURIComponent(id));
    else go('outputs');
  } catch (failure) {
    packageForm.value.error = failure instanceof Error ? failure.message : String(failure);
  } finally {
    packageForm.value.busy = false;
  }
}
</script>

<template>
  <div class="pp-root">
    <div v-if="!worker" class="pp-empty">
      <AppIcon :icon="IconShieldCheck" :size="32" />
      <h3>处理工作区仅向内部成员开放</h3>
      <p>你可以浏览已发布且向你开放的产出包。</p>
      <div class="pp-actions"><button class="pp-button" @click="go('outputs')">查看产出目录</button></div>
    </div>

    <template v-else>
      <div class="ph-section-banner">
        <div class="pp-heading">
          <div>
            <h1>{{ batchId ? '批次处理与抽检' : '处理与抽检' }}</h1>
            <p>{{ batchId ? '处理、抽样、记录问题，验收后生成产出包。' : '把文献组织为批次，跟踪处理和人工抽检。' }}</p>
          </div>
          <div class="pp-actions">
            <button v-if="batchId" class="pp-button" @click="go('batches')"><AppIcon :icon="IconChevronLeft" />批次列表</button>
            <button v-else class="pp-button primary" @click="dialog = { type: 'create' }"><AppIcon :icon="IconPlus" />新建批次</button>
          </div>
        </div>
      </div>

      <div v-if="error" class="pp-notice error" role="alert">
        <AppIcon :icon="IconAlertTriangle" :size="18" /><div>{{ error }}</div>
      </div>

      <div v-if="loading" class="pp-empty">
        <AppIcon :icon="IconLoader2" :size="32" class="pp-spinner" />
        <h3>正在载入批次…</h3>
      </div>

      <!-- 批次列表 -->
      <template v-else-if="!batchId">
        <div class="pp-toolbar">
          <label class="pp-search">
            <AppIcon :icon="IconSearch" :size="17" />
            <input v-model="query" placeholder="搜索批次名称或状态" aria-label="搜索批次" />
          </label>
          <span>{{ available.length }} 个批次</span>
        </div>
        <template v-if="available.length">
          <div class="pp-batch-grid">
            <article v-for="item in available" :key="item.id" class="pp-card pp-batch-card">
              <header>
                <span class="pp-product-icon"><AppIcon :icon="IconLayersIntersect" :size="23" /></span>
                <span :class="['pp-badge', item.status]">{{ statusLabels[item.status] || item.status }}</span>
              </header>
              <h2>{{ cleanTitle(item.name) }}</h2>
              <p>{{ item.document_count ?? item.document_ids?.length ?? 0 }} 篇文献 · {{ item.chapter_count ?? '—' }} 章</p>
              <div class="pp-card-meta">
                <span>抽样比例 {{ Math.round((item.sampling_rate ?? 0.2) * 100) }}%</span>
                <span>{{ formatDate(item.created_at) }}</span>
              </div>
              <footer>
                <span>{{ item.accepted_at ? '验收记录已保留' : '按来源内容分层抽检' }}</span>
                <button @click="go('batches?batch=' + encodeURIComponent(item.id))">查看批次<AppIcon :icon="IconChevronRight" :size="14" /></button>
              </footer>
            </article>
          </div>
        </template>
        <div v-else class="pp-card">
          <div class="pp-empty">
            <h3>{{ query ? '没有匹配的批次' : '从一组文献开始' }}</h3>
            <p>{{ query ? '调整搜索条件后再试。' : '选定文献、提交实际模型处理，再从不同内容类型中随机抽检。' }}</p>
            <div class="pp-actions"><button class="pp-button primary" @click="dialog = { type: 'create' }"><AppIcon :icon="IconPlus" />新建批次</button></div>
          </div>
        </div>
      </template>

      <!-- 批次详情 -->
      <template v-else-if="!batch">
        <div class="pp-card">
          <div class="pp-empty">
            <h3>这个批次不可用</h3>
            <p>返回批次列表重新选择。</p>
          </div>
        </div>
      </template>

      <template v-else>
        <section class="pp-card pp-batch-overview">
          <div>
            <h2>{{ cleanTitle(batch.name) }}</h2>
            <p>{{ detail.documents?.map((document: any) => cleanTitle(document.title)).join(' · ') || '文献清单载入中' }}</p>
            <span class="pp-muted">提示词版本：{{ batch.prompt ? `${batch.prompt.name} · v${batch.prompt.version}` : '提交处理时继承默认版本' }} · 建立于 {{ formatDate(batch.created_at) }}</span>
          </div>
          <span :class="['pp-badge', batch.status]">{{ statusLabels[batch.status] || batch.status }}</span>
        </section>

        <div class="pp-stat-grid">
          <div><span>批次文献</span><strong>{{ detail.documents?.length ?? batch.document_ids?.length ?? 0 }}</strong></div>
          <div><span>当前结果章节</span><strong>{{ chapters.filter((ch: any) => ch.has_result).length }}<small> / {{ chapters.length }}</small></strong></div>
          <div><span>已复核抽样</span><strong>{{ reviewed }}<small> / {{ samples.length }}</small></strong></div>
          <div><span>抽样通过率</span><strong>{{ sampleRate == null ? '—' : sampleRate + '%' }}<small>{{ reviewed ? `${passed}/${reviewed}` : '尚未复核' }}</small></strong></div>
        </div>

        <div class="pp-notice compact">
          <AppIcon :icon="IconShieldCheck" :size="18" />
          <div>通过率只描述已复核样本，不代表全部章节的准确率。未抽样章节保留未人工逐章确认状态。</div>
        </div>

        <div class="pp-batch-actions">
          <button class="pp-button purple" :disabled="!!busy || processing" @click="admin ? (dialog = { type: 'run' }) : run('')">
            <AppIcon :icon="IconArrowRight" />{{ processing ? '实际处理进行中' : '提交模型处理' }}
          </button>
          <button class="pp-button" :disabled="!!busy || processing || !chapters.some((ch: any) => ch.has_result)" @click="action('sample', () => platformRequest('/api/platform/batches/' + encodeURIComponent(batch.id) + '/sample', {}))">
            <AppIcon :icon="IconFlask" />{{ samples.length ? '重新分层抽样' : '分层随机抽样' }}
          </button>
          <button class="pp-button teal" :disabled="!!busy || !detail.can_accept || processing || batch.status === 'accepted'" @click="action('accept', async () => { const value = await platformRequest('/api/platform/batches/' + encodeURIComponent(batch.id) + '/accept', {}); setToast('批次验收已记录，可生成产出包。'); return value; })">
            <AppIcon :icon="IconCheck" />验收批次
          </button>
          <button class="pp-button" :disabled="!!busy || batch.status !== 'accepted'" @click="dialog = { type: 'package' }">
            <AppIcon :icon="IconDownload" />生成产出包
          </button>
        </div>

        <div v-if="jobs[0]?.status === 'error'" class="pp-notice error" role="alert">
          <AppIcon :icon="IconAlertTriangle" :size="18" />
          <div>最近一次处理失败：{{ jobs[0].error || '请检查模型服务配置后重试。' }}</div>
        </div>

        <details v-if="detail.blocking_reasons?.length > 0" class="pp-details">
          <summary>验收还需完成 {{ detail.blocking_reasons.length }} 项</summary>
          <ul><li v-for="(reason, i) in detail.blocking_reasons" :key="i">{{ reason }}</li></ul>
        </details>

        <div class="pp-tabs">
          <button class="active">抽样复核 ({{ samples.length }})</button>
          <span>按文献类型 × 内容类型分层 · 第 {{ batch.sampling_round || 0 }} 轮</span>
        </div>

        <template v-if="samples.length">
          <div class="pp-sample-grid">
            <article v-for="sample in samples" :key="sample.chapter_id + '-' + sample.expected_revision" class="pp-card pp-sample-card">
              <header>
                <span class="pp-chip">{{ stratumLabel(sample) }}</span>
                <span :class="['pp-badge', sample.stale ? 'historical' : sample.outcome || 'unreviewed']">
                  {{ sample.stale ? '版本已变化' : sample.outcome === 'pass' ? '抽检通过' : sample.outcome === 'issue' ? '发现问题' : '待人工检查' }}
                </span>
              </header>
              <h3>{{ sample.title }}</h3>
              <p>固定抽样版本 r{{ sample.expected_revision ?? sample.revision }}</p>
              <blockquote v-if="sample.message">{{ sample.message }}</blockquote>
              <p v-if="sample.stale" class="pp-amber-text">原抽样已过期；重新抽样后复核当前版本。</p>
              <footer>
                <button @click="openChapter(sample)">对照原文<AppIcon :icon="IconArrowRight" :size="14" /></button>
                <div class="pp-actions">
                  <button class="pp-button" :disabled="!!busy || sample.stale || !!sample.outcome" @click="dialog = { type: 'review', sample, outcome: 'issue' }">
                    <AppIcon :icon="IconAlertTriangle" />记问题
                  </button>
                  <button class="pp-button" :disabled="!!busy || sample.stale || !!sample.outcome" @click="dialog = { type: 'review', sample, outcome: 'pass' }">
                    <AppIcon :icon="IconCheck" />记录通过
                  </button>
                </div>
              </footer>
            </article>
          </div>
        </template>
        <div v-else class="pp-card">
          <div class="pp-empty">
            <AppIcon :icon="IconFlask" :size="32" />
            <h3>还没有抽检样本</h3>
            <p>章节已有实际结果后，按论文、植物志等文献类型及图文内容分层随机抽样。</p>
          </div>
        </div>

        <section class="pp-card pp-issue-section">
          <header class="pp-section-heading">
            <h2>问题闭环 <small>{{ openIssues.length }} 项待处理</small></h2>
            <button v-if="admin" class="pp-text-button" @click="go('prompts')">形成规则改进草案<AppIcon :icon="IconArrowRight" :size="14" /></button>
          </header>
          <template v-if="issues.length">
            <article v-for="issue in issues" :key="issue.id" class="pp-issue-row">
              <span :class="['pp-badge', issue.status]">{{ statusLabels[issue.status] || issue.status }}</span>
              <div>
                <p>{{ issue.message }}</p>
                <small>章节 {{ issue.chapter_id }} · r{{ issue.revision }}{{ issue.resolution ? ` · 处理：${issue.resolution}` : '' }}</small>
              </div>
              <button v-if="issue.status === 'open'" class="pp-button" :disabled="!!busy" @click="dialog = { type: 'resolve', issue }">填写闭环说明</button>
            </article>
          </template>
          <p v-else class="pp-muted">当前没有已记录问题。</p>
        </section>

        <details class="pp-card pp-details">
          <summary>章节任务与复核记录</summary>
          <div v-if="jobs.length > 0" class="pp-table-wrap">
            <table>
              <thead><tr><th>处理任务</th><th>状态</th><th>完成章节</th><th>时间</th><th>结果说明</th></tr></thead>
              <tbody>
                <tr v-for="job in jobs" :key="job.id">
                  <td>{{ job.mode === 'model' ? '模型处理' : '资料整理' }}</td>
                  <td><span :class="['pp-badge', job.status]">{{ statusLabels[job.status] || job.status }}</span></td>
                  <td>{{ job.completed || 0 }}/{{ job.chapter_count || 0 }}</td>
                  <td>{{ formatDate(job.created_at) }}</td>
                  <td>{{ job.error || (job.status === 'completed' ? '已保存章节结果' : '按真实状态更新') }}</td>
                </tr>
              </tbody>
            </table>
          </div>
          <div class="pp-table-wrap">
            <table>
              <thead><tr><th>文献 / 章节</th><th>当前版本</th><th>处理与复核状态</th><th>操作</th></tr></thead>
              <tbody>
                <tr v-for="ch in chapters" :key="ch.id || ch.chapter_id">
                  <td><strong>{{ ch.title }}</strong><small>{{ cleanTitle(ch.document_title) }}</small></td>
                  <td>r{{ ch.revision }}</td>
                  <td>
                    <span :class="['pp-badge', sampleStatus(ch)]">{{ statusLabels[sampleStatus(ch)] || sampleStatus(ch) }}</span>
                    <small v-if="!samples.find((s: any) => s.chapter_id === (ch.chapter_id || ch.id)) && ch.has_result">未抽样，不计人工通过</small>
                  </td>
                  <td><button class="pp-text-button" @click="openChapter(ch)">查看</button></td>
                </tr>
              </tbody>
            </table>
          </div>
          <div v-if="detail.reviews?.length > 0" class="pp-table-wrap pp-review-history">
            <table>
              <thead><tr><th>版本</th><th>记录</th><th>说明</th><th>时间</th></tr></thead>
              <tbody>
                <tr v-for="(review, i) in detail.reviews" :key="review.id || i">
                  <td>r{{ review.revision ?? review.expected_revision }}</td>
                  <td><span :class="['pp-badge', review.outcome]">{{ statusLabels[review.outcome] || review.outcome }}</span></td>
                  <td>{{ review.message || '已对照原文检查' }}</td>
                  <td>{{ formatDate(review.created_at) }}</td>
                </tr>
              </tbody>
            </table>
          </div>
        </details>
      </template>

      <!-- 弹窗区 -->
      <AppModal v-if="dialog?.type === 'create'" title="新建处理批次" wide @close="dialog = null">
        <p class="pp-muted">选择文献并确认类型；抽样覆盖各文献类型和图文内容。</p>
        <form @submit.prevent="submitCreate">
          <div class="pp-modal-body">
            <div class="pp-field-grid">
              <label class="pp-field">
                <span>批次名称</span>
                <input v-model="createForm.name" maxlength="200" required :disabled="createForm.busy" placeholder="例如：叶形与生境资料整理" />
              </label>
              <label class="pp-field">
                <span>抽样比例</span>
                <select v-model="createForm.rate" :disabled="createForm.busy">
                  <option :value="0.1">10%</option>
                  <option :value="0.2">20%</option>
                  <option :value="0.5">50%</option>
                  <option :value="1">全部章节</option>
                </select>
              </label>
            </div>
            <label class="pp-search">
              <AppIcon :icon="IconSearch" :size="16" />
              <input v-model="createForm.query" :disabled="createForm.busy" placeholder="搜索待选文献" aria-label="搜索待选文献" />
            </label>
            <div class="pp-document-selection">
              <section v-for="[type, docs] in createGroups" :key="type">
                <h3>{{ literatureLabels[type] }}<small>{{ docs.length }} 篇</small></h3>
                <div v-for="doc in docs" :key="doc.id" class="pp-document-option">
                  <label>
                    <input v-model="createForm.selected" type="checkbox" :value="doc.id" :disabled="createForm.busy" />
                    <span>{{ cleanTitle(doc.title) }}<small>{{ doc.page_count || 0 }} 页 · {{ doc.chapter_count || 0 }} 章</small></span>
                  </label>
                  <select :value="createForm.types[doc.id] || 'other'" :disabled="createForm.busy" :aria-label="'文献类型：' + cleanTitle(doc.title)" @change="createForm.types[doc.id] = ($event.target as HTMLSelectElement).value">
                    <option v-for="[key, label] in Object.entries(literatureLabels)" :key="key" :value="key">{{ label }}</option>
                  </select>
                </div>
              </section>
              <p v-if="!createGroups.length" class="pp-muted">没有可选文献。先到文献目录导入资料。</p>
            </div>
            <p class="pp-muted">已选择 {{ createForm.selected.length }} 篇。每个分层至少抽取 1 章，实际样本数可能高于比例。</p>
            <div v-if="createForm.error" class="pp-notice error" role="alert">
              <AppIcon :icon="IconAlertTriangle" :size="18" /><div>{{ createForm.error }}</div>
            </div>
          </div>
          <footer class="pp-modal-footer">
            <button type="button" class="pp-button" :disabled="createForm.busy" @click="dialog = null">取消</button>
            <button type="submit" class="pp-button primary" :disabled="createForm.busy || !createForm.name.trim() || !createForm.selected.length">
              {{ createForm.busy ? '正在建立…' : '建立批次' }}
            </button>
          </footer>
        </form>
      </AppModal>

      <AppModal v-if="dialog?.type === 'run' && batch" title="提交模型处理" @close="dialog = null">
        <p class="pp-muted">实际调用配置的模型服务。尚未配置时，提交会返回明确错误。</p>
        <div class="pp-modal-body">
          <label class="pp-field">
            <span>提示词版本</span>
            <select v-model="runForm.promptId" :disabled="!!busy">
              <option value="">继承默认已发布版本</option>
              <option v-for="prompt in publishedPrompts" :key="prompt.id" :value="prompt.id">{{ prompt.name }} · v{{ prompt.version }}</option>
            </select>
          </label>
          <p class="pp-muted">任务保留所使用的提示词与原文修订快照，结果到达后才进入抽检。</p>
        </div>
        <footer class="pp-modal-footer">
          <button class="pp-button" :disabled="!!busy" @click="dialog = null">取消</button>
          <button class="pp-button purple" :disabled="!!busy" @click="run(runForm.promptId)">{{ busy ? '正在提交…' : '提交实际处理' }}</button>
        </footer>
      </AppModal>

      <AppModal
        v-if="dialog?.type === 'review' && batch"
        :title="dialog.outcome === 'pass' ? '记录抽检通过' : '记录抽检问题'"
        @close="dialog = null"
      >
        <p class="pp-muted">{{ dialog.sample.title }} · 固定版本 r{{ dialog.sample.expected_revision ?? dialog.sample.revision }}</p>
        <form @submit.prevent="submitReview">
          <div class="pp-modal-body">
            <label class="pp-field">
              <span>{{ dialog.outcome === 'issue' ? '问题说明（必填）' : '检查说明' }}</span>
              <textarea v-model="reviewForm.message" rows="4" :required="dialog.outcome === 'issue'" :disabled="reviewForm.busy" :placeholder="dialog.outcome === 'issue' ? '说明哪一项与原文不一致，以及来源页。' : '记录核对范围和判断依据。'" />
            </label>
            <label v-if="dialog.outcome === 'pass'" class="pp-checkbox">
              <input v-model="reviewForm.checked" type="checkbox" :disabled="reviewForm.busy" />
              我已对照原文检查这个固定版本
            </label>
            <div v-if="reviewForm.error" class="pp-notice error" role="alert">
              <AppIcon :icon="IconAlertTriangle" :size="18" /><div>{{ reviewForm.error }}</div>
            </div>
          </div>
          <footer class="pp-modal-footer">
            <button type="button" class="pp-button" :disabled="reviewForm.busy" @click="dialog = null">取消</button>
            <button type="submit" class="pp-button" :class="dialog.outcome === 'pass' ? 'teal' : 'primary'" :disabled="reviewForm.busy || (dialog.outcome === 'pass' ? !reviewForm.checked : !reviewForm.message.trim())">
              {{ reviewForm.busy ? '正在保存…' : '保存抽检记录' }}
            </button>
          </footer>
        </form>
      </AppModal>

      <AppModal v-if="dialog?.type === 'resolve'" title="记录问题闭环" @close="dialog = null">
        <p class="pp-muted">说明已经如何修正和核对，不会自动将章节改为通过。</p>
        <form @submit.prevent="submitResolve">
          <div class="pp-modal-body">
            <blockquote class="pp-quote">{{ dialog.issue.message }}</blockquote>
            <label class="pp-field">
              <span>处理与复核说明</span>
              <textarea v-model="resolveForm.resolution" rows="4" required :disabled="resolveForm.busy" placeholder="例如：回到来源页纠正单位，保存了新的章节修订。" />
            </label>
            <div v-if="resolveForm.error" class="pp-notice error" role="alert">
              <AppIcon :icon="IconAlertTriangle" :size="18" /><div>{{ resolveForm.error }}</div>
            </div>
          </div>
          <footer class="pp-modal-footer">
            <button type="button" class="pp-button" :disabled="resolveForm.busy" @click="dialog = null">取消</button>
            <button type="submit" class="pp-button primary" :disabled="resolveForm.busy || !resolveForm.resolution.trim()">保存闭环说明</button>
          </footer>
        </form>
      </AppModal>

      <AppModal v-if="dialog?.type === 'package' && batch" title="从验收批次生成产出包" wide @close="dialog = null">
        <p class="pp-muted">包中保留来源修订、抽检记录和文件清单。发布由管理员在产出目录完成。</p>
        <form @submit.prevent="submitPackage">
          <div class="pp-modal-body">
            <label class="pp-field">
              <span>产出包名称</span>
              <input v-model="packageForm.name" required maxlength="200" :disabled="packageForm.busy" />
            </label>
            <div class="pp-field-grid">
              <label class="pp-field">
                <span>产出类型</span>
                <select v-model="packageForm.type" :disabled="packageForm.busy">
                  <option v-for="[key, label] in Object.entries(packageTypes)" :key="key" :value="key">{{ label }}</option>
                </select>
              </label>
              <label v-if="admin" class="pp-field">
                <span>访问范围</span>
                <select v-model="packageForm.role" :disabled="packageForm.busy">
                  <option v-for="[key, label] in Object.entries(packageRoles)" :key="key" :value="key">{{ label }}</option>
                </select>
              </label>
              <div v-else class="pp-field"><span>访问范围</span><div class="pp-fixed-value">内部成员 · 管理员可调整发布范围</div></div>
            </div>
            <label v-if="packageForm.type === 'kb'" class="pp-field">
              <span>知识库切分长度</span>
              <select v-model="packageForm.chunkSize" :disabled="packageForm.busy">
                <option :value="500">500 字符 · 较细切分</option>
                <option :value="900">900 字符 · 标准切分</option>
                <option :value="1400">1400 字符 · 保留更多上下文</option>
              </select>
              <small>按正文段落切分，过长段落按所选长度分段。</small>
            </label>
            <p class="pp-muted">缺少符合类型的数据时会明确失败，不自动编造问答、偏好或图文样本。</p>
            <div v-if="packageForm.error" class="pp-notice error" role="alert">
              <AppIcon :icon="IconAlertTriangle" :size="18" /><div>{{ packageForm.error }}</div>
            </div>
          </div>
          <footer class="pp-modal-footer">
            <button type="button" class="pp-button" :disabled="packageForm.busy" @click="dialog = null">取消</button>
            <button type="submit" class="pp-button teal" :disabled="packageForm.busy || !packageForm.name.trim()">
              {{ packageForm.busy ? '正在生成…' : '生成产出包' }}
            </button>
          </footer>
        </form>
      </AppModal>
    </template>
  </div>
</template>
