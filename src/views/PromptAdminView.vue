<script setup lang="ts">
// 提示词与规则（对照 PromptAdmin.jsx PromptAdmin）
import { computed, onMounted, ref, watch } from 'vue';
import {
  IconCheck,
  IconEdit,
  IconFileText,
  IconHistory,
  IconPlus,
  IconRefresh,
  IconSearch,
  IconShieldCheck,
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

const admin = computed(() => props.user?.role === 'admin');

const prompts = ref<any[]>([]);
const standards = ref<any[]>([]);
const batches = ref<any[]>([]);
const jobs = ref<any[]>([]);
const tab = ref('generic');
const selectedId = ref(readHash().get('prompt') || '');
const query = ref('');
const loading = ref(true);
const error = ref('');
const busy = ref('');
const dialog = ref<any>(null);
let lock = false;

async function refresh(): Promise<void> {
  if (!admin.value) {
    loading.value = false;
    return;
  }
  try {
    const [p, s, b] = await Promise.all([
      platformRequest('/api/platform/prompts'),
      platformRequest('/api/platform/standards'),
      platformRequest('/api/platform/batches'),
    ]);
    prompts.value = p.prompts || [];
    standards.value = s.standards || [];
    jobs.value = s.jobs || [];
    batches.value = b.batches || [];
    error.value = '';
    loading.value = false;
  } catch (failure) {
    error.value = failure instanceof Error ? failure.message : String(failure);
    loading.value = false;
  }
}

onMounted(refresh);

watch(
  () => workspace.routeHash,
  () => {
    if (routeOf(workspace.routeHash) !== 'prompts') return;
    const id = readHash().get('prompt');
    if (id) selectedId.value = id;
  },
);

watch(jobs, (list) => {
  if (!list.some((job) => ['queued', 'processing', 'running'].includes(job.status))) return;
  const timer = setInterval(refresh, 2000);
  return () => clearInterval(timer);
});

async function action(name: string, fn: () => Promise<unknown>): Promise<unknown> {
  if (lock) return null;
  lock = true;
  busy.value = name;
  error.value = '';
  try {
    const result = await fn();
    await refresh();
    return result;
  } catch (failure) {
    error.value = failure instanceof Error ? failure.message : String(failure);
    setToast(error.value);
    return null;
  } finally {
    busy.value = '';
    lock = false;
  }
}

const values = computed(() =>
  (tab.value === 'standards' ? standards.value : prompts.value.filter((item) => item.type === tab.value)).filter(
    (item) => !query.value || [item.name, item.title, item.content].some((value) => String(value || '').toLowerCase().includes(query.value.toLowerCase())),
  ),
);

const selected = computed(() => values.value.find((item) => item.id === selectedId.value) || null);
const newestJob = computed(() => jobs.value[0]);
const selectedJob = computed(() => jobs.value.find((job) => job.standard_id === selectedId.value));
const selectedProcessing = computed(
  () => tab.value === 'standards' && ['queued', 'processing', 'running'].includes(selectedJob.value?.status),
);

function switchTab(key: string): void {
  tab.value = key;
  selectedId.value = '';
  query.value = '';
}

async function onSavedPrompt(value: any): Promise<void> {
  const promptType = dialog.value?.promptType ?? tab.value;
  await refresh();
  dialog.value = null;
  tab.value = promptType;
  const id = value.prompt?.id || value.id || '';
  if (id) selectedId.value = id;
  setToast('提示词新版本已保存。');
}

async function onSavedDraft(value: any): Promise<void> {
  await refresh();
  dialog.value = null;
  tab.value = 'standards';
  const id = value.standard?.id || '';
  if (id) selectedId.value = id;
  setToast(value.job ? '草案任务已提交，结果按真实状态更新。' : '问题规则草案已生成，等待人工修改和发布。');
}

async function onSavedStandard(): Promise<void> {
  await refresh();
  dialog.value = null;
  setToast('草案修改已保存。');
}

// —— 三个编辑弹窗的本地表单状态与提交 ——
const promptForm = ref({ name: '', content: '', status: 'draft', defaultChoice: false, busy: false, error: '' });
const draftForm = ref<{ ids: string[]; mode: string; busy: boolean; error: string }>({ ids: [], mode: 'manual', busy: false, error: '' });
const standardForm = ref({ title: '', content: '', busy: false, error: '' });

watch(dialog, (d) => {
  if (!d) return;
  if (d.type === 'prompt') {
    promptForm.value = { name: d.item?.name || '', content: d.item?.content || '', status: 'draft', defaultChoice: false, busy: false, error: '' };
  } else if (d.type === 'feedback') {
    draftForm.value = { ids: [], mode: 'manual', busy: false, error: '' };
  } else if (d.type === 'standardEdit') {
    standardForm.value = { title: d.item?.title || '', content: d.item?.content || '', busy: false, error: '' };
  }
});

async function submitPrompt(): Promise<void> {
  promptForm.value.busy = true;
  promptForm.value.error = '';
  try {
    const value = await platformRequest(
      '/api/platform/prompts' + (dialog.value.item ? '/' + encodeURIComponent(dialog.value.item.id) : ''),
      {
        name: promptForm.value.name.trim(),
        type: dialog.value.promptType,
        content: promptForm.value.content.trim(),
        status: promptForm.value.status,
        select_default:
          promptForm.value.status === 'published' && dialog.value.promptType === 'generic' && promptForm.value.defaultChoice,
        ...(dialog.value.item ? { expected_version: dialog.value.item.version } : {}),
      },
    );
    await onSavedPrompt(value);
  } catch (failure) {
    promptForm.value.error = failure instanceof Error ? failure.message : String(failure);
  } finally {
    promptForm.value.busy = false;
  }
}

async function submitDraft(): Promise<void> {
  draftForm.value.busy = true;
  draftForm.value.error = '';
  try {
    const value = await platformRequest('/api/platform/standards/draft', {
      ...(draftForm.value.ids.length ? { batch_ids: draftForm.value.ids } : {}),
      mode: draftForm.value.mode,
    });
    await onSavedDraft(value);
  } catch (failure) {
    draftForm.value.error = failure instanceof Error ? failure.message : String(failure);
  } finally {
    draftForm.value.busy = false;
  }
}

async function submitStandard(): Promise<void> {
  standardForm.value.busy = true;
  standardForm.value.error = '';
  try {
    await platformRequest('/api/platform/standards/' + encodeURIComponent(dialog.value.item.id), {
      title: standardForm.value.title.trim(),
      content: standardForm.value.content.trim(),
    });
    await onSavedStandard();
  } catch (failure) {
    standardForm.value.error = failure instanceof Error ? failure.message : String(failure);
  } finally {
    standardForm.value.busy = false;
  }
}
</script>

<template>
  <div class="pp-root">
    <div v-if="!admin" class="pp-empty">
      <AppIcon :icon="IconShieldCheck" :size="32" />
      <h3>提示词与规则管理仅向管理员开放</h3>
      <p>工作成员的处理任务会继承管理员发布的默认版本。</p>
    </div>

    <template v-else>
      <div class="ph-section-banner">
        <div class="page-heading">
          <div>
            <h1>提示词与规则</h1>
            <p>发布通用整理提示词、专门字段词典，并把抽检问题转为改进草案。</p>
          </div>
          <div class="heading-actions">
            <button
              class="button primary"
              @click="dialog = tab === 'standards' ? { type: 'feedback' } : { type: 'prompt', promptType: tab }"
            >
              <AppIcon :icon="IconPlus" />{{ tab === 'standards' ? '从问题生成草案' : '新建版本' }}
            </button>

          </div>
        </div>
      </div>

      <div v-if="error" class="pp-notice error" role="alert">
        <AppIcon :icon="IconRefresh" :size="18" /><div>{{ error }}</div>
      </div>
      <div v-if="newestJob?.status === 'error'" class="pp-notice error" role="alert">
        <AppIcon :icon="IconRefresh" :size="18" />
        <div>最近一次草案处理失败：{{ newestJob.error || '请检查模型服务配置。' }}</div>
      </div>

      <div class="pp-tabs">
        <button v-for="[key, label] in [['generic', '通用提示词'], ['special', '专门字段词典'], ['standards', '反馈改进草案']]" :key="key" :class="{ active: tab === key }" @click="switchTab(key)">{{ label }}</button>
      </div>

      <div v-if="loading" class="pp-empty">
        <AppIcon :icon="IconRefresh" :size="32" class="pp-spinner" />
        <h3>正在载入提示词版本…</h3>
      </div>

      <template v-else>
        <div class="pp-toolbar">
          <label class="pp-search">
            <AppIcon :icon="IconSearch" :size="17" />
            <input v-model="query" aria-label="搜索提示词版本" placeholder="搜索名称或内容" />
          </label>
          <span>{{ values.length }} 个版本</span>
        </div>

        <div class="pp-admin-layout">
          <section class="pp-card pp-version-list">
            <template v-if="values.length">
              <button
                v-for="item in values"
                :key="item.id"
                :class="{ active: item.id === selectedId }"
                @click="selectedId = item.id"
              >
                <div>
                  <strong>{{ item.name || item.title }}</strong>
                  <small>{{ tab === 'standards' ? (item.origin === 'model' ? '模型生成草案' : '问题规则草案') : `v${item.version}` }}{{ item.is_default ? ' · 默认' : '' }}</small>
                </div>
                <span :class="['pp-badge', item.status]">{{ statusLabels[item.status] || item.status }}</span>
                <small>{{ formatDate(item.created_at) }}</small>
              </button>
            </template>
            <div v-else class="pp-empty">
              <AppIcon :icon="IconFileText" :size="32" />
              <h3>暂无对应版本</h3>
              <p>建立新版本，或从已记录问题整理改进草案。</p>
            </div>
          </section>

          <section class="pp-card pp-version-detail">
            <template v-if="selected">
              <header>
                <div>
                  <h2>{{ selected.name || selected.title }}</h2>
                  <p>{{ tab === 'standards' ? `${selected.origin === 'model' ? '实际模型生成' : '根据问题生成的规则草案'} · ${formatDate(selected.created_at)}` : `版本 v${selected.version} · ${selected.type === 'generic' ? '通用整理提示词' : '专门字段词典'} · ${formatDate(selected.created_at)}` }}</p>
                </div>
                <span :class="['pp-badge', selected.status]">{{ statusLabels[selected.status] || selected.status }}</span>
              </header>
              <div v-if="selectedProcessing" class="pp-notice">
                <AppIcon :icon="IconShieldCheck" :size="18" />
                <div>实际模型草案正在生成，状态会自动更新；完成后再人工修改和发布。</div>
              </div>
              <pre class="pp-prompt-content">{{ selected.content || (selectedProcessing ? '正在等待模型返回草案…' : '当前版本尚无内容。') }}</pre>

              <template v-if="tab === 'standards'">
                <p class="pp-muted">来自 {{ selected.batch_ids?.length || 0 }} 个批次、{{ selected.issue_ids?.length || 0 }} 条问题。{{ selected.origin === 'model' ? '本草案来自实际模型任务，发布前需核对建议。' : '本草案直接整理问题记录，未调用模型。' }}</p>
                <div class="pp-actions">
                  <button v-if="selected.status === 'draft'" class="pp-button" :disabled="selectedProcessing" @click="dialog = { type: 'standardEdit', item: selected }">
                    <AppIcon :icon="IconEdit" />修改草案
                  </button>
                  <button
                    class="pp-button teal"
                    :disabled="!!busy || selected.status !== 'draft' || selectedProcessing || !selected.content?.trim()"
                    @click="action('publish', async () => { const value = await platformRequest('/api/platform/standards/' + encodeURIComponent(selected.id) + '/publish', {}); setToast('规则草案已发布，后续任务会保留发布快照。'); return value; })"
                  >
                    <AppIcon :icon="IconCheck" />发布改进规则
                  </button>
                </div>
              </template>

              <template v-else>
                <div class="pp-actions">
                  <button class="pp-button" @click="dialog = { type: 'prompt', item: selected, promptType: selected.type }">
                    <AppIcon :icon="IconPlus" />以此创建新版本
                  </button>
                  <button
                    v-if="selected.type === 'generic'"
                    class="pp-button teal"
                    :disabled="!!busy || selected.status !== 'published' || selected.is_default"
                    @click="action('default', async () => { const value = await platformRequest('/api/platform/prompts/' + encodeURIComponent(selected.id) + '/default', {}); setToast('默认通用提示词已更新，历史任务仍保留原版本。'); return value; })"
                  >
                    <AppIcon :icon="IconCheck" />{{ selected.is_default ? '已是默认版本' : '设为默认' }}
                  </button>
                </div>
                <p class="pp-muted">旧版本保留，发布新版本不会修改历史批次的提示词快照。专门字段词典按任务选用；只有已发布通用提示词能设为默认。</p>
              </template>
            </template>

            <div v-else class="pp-empty">
              <AppIcon :icon="IconHistory" :size="32" />
              <h3>选择一个版本查看内容</h3>
              <p>版本内容与反馈规则只向管理员展示。</p>
            </div>
          </section>
        </div>
      </template>
    </template>

    <!-- 新建 / 修订提示词版本 -->
    <AppModal
      v-if="dialog?.type === 'prompt'"
      :title="dialog.item ? '保存提示词新版本' : '创建提示词版本'"
      :wide="true"
      @close="dialog = null"
    >
      <p class="pp-muted">{{ dialog.promptType === 'special' ? '专门字段词典：明确字段含义、原文证据、类型与缺失处理。' : '通用整理提示词：描述原文、图文产物与来源要求。' }}</p>
      <form @submit.prevent="submitPrompt">
        <div class="pp-modal-body">
          <label class="pp-field">
            <span>版本名称</span>
            <input v-model="promptForm.name" required maxlength="200" :disabled="promptForm.busy" :placeholder="dialog.promptType === 'special' ? '例如：植物形态与生境字段词典' : '例如：文献章节整理规则'" />
          </label>
          <label class="pp-field">
            <span>内容</span>
            <textarea v-model="promptForm.content" class="pp-prompt-editor" rows="12" required :disabled="promptForm.busy" placeholder="写明要求、证据引用和缺失信息处理。不要要求模型补齐原文没有的结论。" />
          </label>
          <div class="pp-field-grid">
            <label class="pp-field">
              <span>保存状态</span>
              <select v-model="promptForm.status" :disabled="promptForm.busy">
                <option value="draft">草案 · 暂不用于处理</option>
                <option value="published">已发布 · 可用于新任务</option>
              </select>
            </label>
            <label v-if="dialog.promptType === 'generic' && promptForm.status === 'published'" class="pp-checkbox">
              <input v-model="promptForm.defaultChoice" type="checkbox" :disabled="promptForm.busy" />
              发布后设为默认通用提示词
            </label>
          </div>
          <div v-if="promptForm.error" class="pp-notice error" role="alert">
            <AppIcon :icon="IconCheck" :size="18" /><div>{{ promptForm.error }}</div>
          </div>
        </div>
        <footer class="pp-modal-footer">
          <button type="button" class="pp-button" @click="dialog = null" :disabled="promptForm.busy">取消</button>
          <button type="submit" class="pp-button primary" :disabled="promptForm.busy || !promptForm.name.trim() || !promptForm.content.trim()">
            {{ promptForm.busy ? '正在保存…' : promptForm.status === 'published' ? '发布新版本' : '保存草案版本' }}
          </button>
        </footer>
      </form>
    </AppModal>

    <!-- 从抽检问题生成改进草案 -->
    <AppModal v-if="dialog?.type === 'feedback'" title="从抽检问题形成改进草案" :wide="true" @close="dialog = null">
      <p class="pp-muted">收集实际问题记录，生成可人工修改的规则建议；发布后才用于后续任务。</p>
      <form @submit.prevent="submitDraft">
        <div class="pp-modal-body">
          <label class="pp-field">
            <span>草案生成方式</span>
            <select v-model="draftForm.mode" :disabled="draftForm.busy">
              <option value="manual">按问题整理规则草案</option>
              <option value="model">调用已配置模型生成</option>
              <option value="auto">自动选择可用方式</option>
            </select>
          </label>
          <p class="pp-muted">规则方式直接整理已记录问题；模型方式会实际调用服务，未配置时明确失败。自动方式按真实来源标注结果。</p>
          <div class="pp-feedback-batches">
            <label v-for="batch in batches" :key="batch.id">
              <input v-model="draftForm.ids" type="checkbox" :value="batch.id" :disabled="draftForm.busy" />
              <span>{{ cleanTitle(batch.name) }}</span>
              <span :class="['pp-badge', batch.status]">{{ statusLabels[batch.status] || batch.status }}</span>
            </label>
          </div>
          <p class="pp-muted">未选择批次时使用全部可用问题记录。</p>
          <div v-if="draftForm.error" class="pp-notice error" role="alert">
            <AppIcon :icon="IconCheck" :size="18" /><div>{{ draftForm.error }}</div>
          </div>
        </div>
        <footer class="pp-modal-footer">
          <button type="button" class="pp-button" @click="dialog = null" :disabled="draftForm.busy">取消</button>
          <button type="submit" class="pp-button primary" :disabled="draftForm.busy">{{ draftForm.busy ? '正在提交…' : '生成改进草案' }}</button>
        </footer>
      </form>
    </AppModal>

    <!-- 人工修改反馈草案 -->
    <AppModal v-if="dialog?.type === 'standardEdit'" title="人工修改反馈草案" :wide="true" @close="dialog = null">
      <p class="pp-muted">先核对建议与来源问题，保存后再发布。</p>
      <form @submit.prevent="submitStandard">
        <div class="pp-modal-body">
          <label class="pp-field">
            <span>草案标题</span>
            <input v-model="standardForm.title" required :disabled="standardForm.busy" />
          </label>
          <label class="pp-field">
            <span>改进规则</span>
            <textarea v-model="standardForm.content" class="pp-prompt-editor" rows="12" required :disabled="standardForm.busy" />
          </label>
          <div v-if="standardForm.error" class="pp-notice error" role="alert">
            <AppIcon :icon="IconCheck" :size="18" /><div>{{ standardForm.error }}</div>
          </div>
        </div>
        <footer class="pp-modal-footer">
          <button type="button" class="pp-button" @click="dialog = null" :disabled="standardForm.busy">取消</button>
          <button type="submit" class="pp-button primary" :disabled="standardForm.busy || !standardForm.title.trim() || !standardForm.content.trim()">保存草案修改</button>
        </footer>
      </form>
    </AppModal>
  </div>
</template>
