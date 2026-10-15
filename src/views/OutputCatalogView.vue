<script setup lang="ts">
// 产出目录（对照 OutputCatalog.jsx OutputCatalog + KnowledgeSearch 独立页）
import { computed, onMounted, ref, watch } from 'vue';
import {
  IconArrowRight,
  IconBook2,
  IconChevronLeft,
  IconDatabase,
  IconDownload,
  IconFileText,
  IconSearch,
  IconShieldCheck,
  IconSettings,
  IconRefresh,
  IconExternalLink,
  IconX,
} from '@tabler/icons-vue';
import AppIcon from '../components/common/AppIcon.vue';
import AppModal from '../components/common/AppModal.vue';
import { platformRequest } from '../api/client';
import { go, setToast, workspace, routeOf } from '../stores/workspace';
import { readHash } from '../composables/useHashRoute';
import { cleanTitle, date as formatDate } from '../utils/format';
import { mockPackages } from '../data/mockPackages';

const props = defineProps<{ user: any }>();

const typeNames: Record<string, string> = {
  pretrain: '正文训练集',
  sft: '问答训练集',
  preference: '偏好训练集',
  image: '图文训练集',
  kb: '知识库',
};
// 产出包访问范围（与用户角色文案不同）
const packageRoles: Record<string, string> = {
  admin: '仅管理员',
  internal: '内部成员',
  external: '外部成员',
};

const bytes = (n: number): string =>
  Number(n) >= 1048576 ? `${(Number(n) / 1048576).toFixed(1)} MiB` : `${Math.max(0, Math.ceil(Number(n || 0) / 1024))} KiB`;

const packageLink = (value: unknown): string | null =>
  typeof value === 'string' && /^\/api\/packages\/[A-Za-z0-9_-]+\/download(?:\?.*)?$/.test(value) ? value : null;

const sampleLabel = (sample: any): string =>
  sample?.reviewed > 0
    ? `${Math.round(Number(sample.rate ?? sample.passed / sample.reviewed) * 100)}% · ${sample.passed}/${sample.reviewed}`
    : '尚无已复核样本';

const metadataOf = (item: any): any => item?.metadata || {};

const packageId = ref(readHash().get('package') || '');
const packages = ref<any[]>([]);
const selected = ref<any>(null);
// 卡片选中态（点击卡片高亮，刷新后恢复）
const selectedCardId = ref('');

function selectCard(item: any): void {
  selectedCardId.value = selectedCardId.value === item.id ? '' : item.id;
}
const query = ref('');
const type = ref('all');
const loading = ref(true);
const error = ref('');
const dialog = ref(false);
const tab = ref('preview');

const admin = computed(() => props.user?.role === 'admin');
const worker = computed(() => admin.value || props.user?.role === 'internal');

async function refresh(): Promise<void> {
  selectedCardId.value = '';
  try {
    const listed = await platformRequest('/api/packages');
    const real = listed.packages || [];
    packages.value = [...mockPackages, ...real];
    const mock = packageId.value ? mockPackages.find((item) => item.id === packageId.value) : null;
    if (mock) {
      selected.value = mock;
      error.value = '';
      loading.value = false;
      return;
    }
    const detail = packageId.value ? await platformRequest('/api/packages/' + encodeURIComponent(packageId.value)) : null;
    selected.value = detail?.package || null;
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
    if (routeOf(workspace.routeHash) !== 'outputs') return;
    const id = readHash().get('package') || '';
    packageId.value = id;
    selected.value = null;
    loading.value = true;
    dialog.value = false;
    tab.value = 'preview';
    void refresh();
  },
);

const items = computed(() =>
  packages.value.filter(
    (item) =>
      (type.value === 'all' || (type.value === 'training' && item.type !== 'kb') || item.type === type.value) &&
      (!query.value || [item.name, typeNames[item.type]].some((value) => String(value || '').toLowerCase().includes(query.value.toLowerCase()))),
  ),
);

const meta = computed(() => metadataOf(selected.value));
const sample = computed(() => meta.value.sample_pass_rate);
const download = computed(() => packageLink(selected.value?.download_url));
const publishForm = ref({ published: false, role: 'internal' });

function openPublish(): void {
  publishForm.value = {
    published: !!selected.value?.published,
    role: selected.value?.minimum_role || 'internal',
  };
  dialog.value = true;
}

async function savePublish(): Promise<void> {
  // 合成示例包仅在前端演示发布状态，不写入后端真实数据
  if (selected.value?.isMock) {
    selected.value.published = publishForm.value.published;
    selected.value.minimum_role = publishForm.value.role;
    dialog.value = false;
    setToast('发布状态与访问范围已保存。');
    return;
  }
  await platformRequest('/api/packages/' + encodeURIComponent(selected.value.id) + '/publish', {
    published: publishForm.value.published,
    minimum_role: publishForm.value.role,
  });
  await refresh();
  dialog.value = false;
  setToast('发布状态与访问范围已保存。');
}
</script>

<template>
  <div class="pp-root">
    <div class="ph-section-banner">
      <div class="page-heading">
        <div>
          <h1>{{ packageId ? '产出包详情' : '产出目录' }}</h1>
          <p>{{ packageId ? '查看内容、来源、抽检记录与访问范围。' : '浏览已经生成的训练集与知识库，生成操作在批次工作区完成。' }}</p>
        </div>
        <div class="heading-actions">
          <button v-if="packageId" class="button" @click="go('outputs')">
            <AppIcon :icon="IconChevronLeft" />返回目录
          </button>
          <button v-else-if="worker" class="button" @click="go('batches')">
            <AppIcon :icon="IconShieldCheck" />前往处理抽检区
          </button>
        </div>
      </div>
    </div>

    <div v-if="error" class="pp-notice error" role="alert">
      <AppIcon :icon="IconX" :size="18" /><div>{{ error }}</div>
    </div>

    <div v-if="loading" class="pp-empty">
      <AppIcon :icon="IconRefresh" :size="32" class="pp-spinner" />
      <h3>正在载入产出包…</h3>
    </div>

    <template v-else-if="!packageId">
      <div class="pp-toolbar">
        <label class="pp-search">
          <AppIcon :icon="IconSearch" :size="17" />
          <input v-model="query" aria-label="搜索产出包" placeholder="搜索包名称或类型" />
        </label>
        <select v-model="type" aria-label="筛选产出类型">
          <option value="all">全部类型</option>
          <option value="training">训练数据</option>
          <option value="kb">知识库</option>
        </select>
        <div class="pp-toolbar-meta">
          <span>{{ items.length }} 个包</span>
          <button class="icon-button" aria-label="刷新产出目录" @click="refresh"><AppIcon :icon="IconRefresh" :size="15" /></button>
        </div>
      </div>

      <div v-if="items.length" class="pp-package-grid">
        <article
          v-for="item in items"
          :key="item.id"
          class="pp-card pp-package-card"
          :class="{ 'is-selected': selectedCardId === item.id }"
          role="button"
          tabindex="0"
          @click="selectCard(item)"
          @keydown.enter="selectCard(item)"
        >
          <header>
            <span :class="['pp-product-icon', item.type === 'kb' ? 'teal' : 'purple']">
              <AppIcon :icon="item.type === 'kb' ? IconDatabase : IconFileText" :size="23" />
            </span>
            <div class="pp-actions">
              <span :class="['pp-badge', metadataOf(item).audit_status === 'historical' || metadataOf(item).revision_eligible === false ? 'historical' : item.published ? 'published' : 'ready']">
                {{ metadataOf(item).audit_status === 'historical' || metadataOf(item).revision_eligible === false ? '历史版本' : item.published ? '已发布' : '未发布' }}
              </span>
            </div>
          </header>
          <h2>{{ cleanTitle(item.name) }}</h2>
          <p>{{ typeNames[item.type] || item.type }} · {{ metadataOf(item).source_document_count ?? item.source_documents?.length ?? 0 }} 篇来源文献</p>
          <div class="pp-package-summary">
            <span>{{ item.type === 'kb' ? metadataOf(item).chunk_count ?? metadataOf(item).record_count ?? 0 : metadataOf(item).record_count ?? 0 }}<small>{{ item.type === 'kb' ? '检索片段' : '记录' }}</small></span>
            <span>{{ sampleLabel(metadataOf(item).sample_pass_rate) }}<small>已复核样本通过率</small></span>
          </div>
          <div class="pp-card-meta">
            <span>{{ packageRoles[item.minimum_role] || item.minimum_role }}</span>
            <span>{{ formatDate(item.created_at) }}</span>
          </div>
          <footer>
            <span>{{ metadataOf(item).audit_status === 'historical' || metadataOf(item).revision_eligible === false ? '保留文件，当前审核资格已变化' : '保留批次验收快照' }}</span>
            <button class="pp-package-open" @click.stop="go('outputs?package=' + encodeURIComponent(item.id))">查看包<AppIcon :icon="IconChevronLeft" :size="14" class="pp-arrow-forward" /></button>
          </footer>
        </article>
      </div>
      <div v-else class="pp-card">
        <div class="pp-empty">
          <h3>{{ query ? '没有匹配的产出包' : '暂无可浏览的产出包' }}</h3>
          <p v-if="!query">{{ user.role === 'external' ? '管理员发布并开放的包会显示在这里。' : '先在批次工作区处理、抽检和验收，再生成训练集或知识库。' }}</p>
          <div v-if="!query && worker" class="pp-actions">
            <button class="pp-button" @click="go('batches')"><AppIcon :icon="IconShieldCheck" />进入批次工作区</button>
          </div>
        </div>
      </div>
    </template>

    <template v-else-if="!selected">
      <div class="pp-card">
        <div class="pp-empty">
          <h3>产出包不存在或无访问权限</h3>
          <p>返回目录查看你当前可以访问的包。</p>
        </div>
      </div>
    </template>

    <template v-else>
      <section class="pp-card pp-package-heading">
        <span :class="['pp-product-icon', selected.type === 'kb' ? 'teal' : 'purple']">
          <AppIcon :icon="selected.type === 'kb' ? IconDatabase : IconFileText" :size="28" />
        </span>
        <div>
          <h2>{{ cleanTitle(selected.name) }}</h2>
          <p>{{ typeNames[selected.type] || selected.type }} · {{ formatDate(selected.created_at) }} · {{ packageRoles[selected.minimum_role] || selected.minimum_role }}</p>
        </div>
        <div class="pp-actions">

          <span :class="['pp-badge', selected.published ? 'published' : 'ready']">{{ selected.published ? '已发布' : '未发布' }}</span>
          <button v-if="admin" class="pp-button" @click="openPublish"><AppIcon :icon="IconSettings" />发布与权限</button>
          <a v-if="download" class="pp-button primary" :href="download" download><AppIcon :icon="IconDownload" :size="17" />下载文件包</a>
        </div>
      </section>

      <div v-if="meta.audit_status === 'historical' || meta.revision_eligible === false" class="pp-notice">
        <AppIcon :icon="IconShieldCheck" :size="18" />
        <div>这是保留的历史产出版本。来源修订或审核资格已变化，文件仍保留；它不代表当前批次已经验收。</div>
      </div>

      <div class="pp-stat-grid">
        <div><span>来源文献</span><strong>{{ meta.source_document_count ?? selected.source_documents?.length ?? 0 }}</strong></div>
        <div><span>{{ selected.type === 'kb' ? '检索片段' : '内容记录' }}</span><strong>{{ selected.type === 'kb' ? meta.chunk_count ?? meta.record_count ?? 0 : meta.record_count ?? 0 }}</strong></div>
        <div><span>图片 / 文件</span><strong>{{ meta.image_count ?? 0 }}<small> / {{ meta.file_count ?? selected.files?.length ?? 0 }}</small></strong></div>
        <div><span>已复核样本通过率</span><strong>{{ sample?.reviewed > 0 ? `${Math.round(Number(sample.rate) * 100)}%` : '—' }}<small>{{ sample?.reviewed > 0 ? `${sample.passed}/${sample.reviewed}` : '尚未复核' }}</small></strong></div>
      </div>

      <div class="pp-notice">
        <AppIcon :icon="IconShieldCheck" :size="18" />
        <div>抽检统计只覆盖已复核样本，不是总体准确率。样本 {{ sample?.sampled ?? 0 }} 章 / 来源 {{ sample?.total_chapters ?? 0 }} 章；审核资格：{{ meta.revision_eligible === false ? '历史快照' : meta.audit_status === 'accepted' ? '批次已验收' : '以记录为准' }}。</div>
      </div>

      <div class="pp-tabs">
        <button v-for="[key, label] in [['preview', '内容预览'], ['sources', '来源与处理'], ['files', '文件清单']]" :key="key" :class="{ active: tab === key }" @click="tab = key">{{ label }}</button>
      </div>

      <section class="pp-card pp-package-detail">
        <template v-if="tab === 'preview'">
          <template v-if="selected.previews?.length">
            <article v-for="(item, index) in selected.previews" :key="index" class="pp-preview-record">
              <header>
                <span>记录 {{ index + 1 }}</span>
                <small v-if="Array.isArray(item.metadata?.source_pages)">来源页：{{ item.metadata.source_pages.join('、') }}</small>
              </header>
              <div v-if="Array.isArray(item.messages || item.record?.messages)" class="pp-message-preview">
                <div v-for="(message, i) in (item.messages || item.record.messages)" :key="i">
                  <span class="pp-badge ready">{{ message.role }}</span>
                  <p>{{ typeof message.content === 'string' ? message.content : JSON.stringify(message.content, null, 2) }}</p>
                </div>
              </div>
              <pre v-else-if="item.text ?? item.content ?? item.record?.text">{{ typeof (item.text ?? item.content ?? item.record?.text) === 'string' ? (item.text ?? item.content ?? item.record?.text) : JSON.stringify(item.text ?? item.content ?? item.record?.text, null, 2) }}</pre>
              <pre v-else>{{ JSON.stringify(item, null, 2) }}</pre>
            </article>
          </template>
          <div v-else class="pp-empty">
            <AppIcon :icon="IconFileText" :size="32" />
            <h3>这个包没有在线预览记录</h3>
            <p>可查看文件清单或下载完整包。</p>
          </div>
          <button v-if="selected.type === 'kb'" class="pp-button" @click="go('knowledge?package=' + encodeURIComponent(selected.id))"><AppIcon :icon="IconSearch" />检索这个知识库</button>
        </template>

        <template v-else-if="tab === 'sources'">
          <h3>来源文献</h3>
          <template v-if="selected.source_documents?.length">
            <div v-for="document in selected.source_documents" :key="document.id" class="pp-source-row">
              <AppIcon :icon="IconBook2" :size="19" />
              <div><strong>{{ cleanTitle(document.title) }}</strong><small>{{ document.page_count || 0 }} 页 · 文献 {{ document.id }}</small></div>
              <button v-if="worker" class="pp-text-button" @click="go('chapters?document=' + encodeURIComponent(document.id))">查看原文<AppIcon :icon="IconExternalLink" :size="13" /></button>
            </div>
          </template>
          <p v-else class="pp-muted">没有来源文献元数据。</p>
          <p v-if="!worker" class="pp-muted">来源页文字见包内 documents/ 文件及来源清单；当前账号只浏览产出，不进入内部文献工作区。</p>
          <h3 class="pp-spaced-title">每日处理量</h3>
          <template v-if="Object.entries(meta.processed_by_day || {}).length || Array.isArray(meta.processed_by_day)">
            <div class="pp-table-wrap">
              <table>
                <thead><tr><th>日期</th><th>处理文献数</th></tr></thead>
                <tbody>
                  <tr v-for="(row, index) in Array.isArray(meta.processed_by_day) ? meta.processed_by_day : Object.entries(meta.processed_by_day || {}).map(([d, count]) => ({ date: d, count }))" :key="index">
                    <td>{{ row.date || row.day }}</td>
                    <td>{{ row.source_document_count ?? row.count ?? row.processed ?? row.documents ?? 0 }}</td>
                  </tr>
                </tbody>
              </table>
            </div>
          </template>
          <p v-else class="pp-muted">暂无可汇总的每日处理记录。</p>
          <p class="pp-muted">这里只汇总本包来源的处理记录。历史文件和审核快照保留，后续修订不会覆盖旧包。</p>
        </template>

        <template v-else>
          <div v-if="selected.files?.length" class="pp-table-wrap">
            <table>
              <thead><tr><th>文件</th><th>大小</th></tr></thead>
              <tbody>
                <tr v-for="(file, index) in selected.files" :key="file.path || index"><td>{{ file.path }}</td><td>{{ bytes(file.size) }}</td></tr>
              </tbody>
            </table>
          </div>
          <p v-else class="pp-muted">没有文件清单。</p>
          <a v-if="download" class="pp-button primary pp-spaced-title" :href="download" download><AppIcon :icon="IconDownload" :size="17" />下载完整文件包</a>
        </template>
      </section>
    </template>

    <AppModal v-if="dialog && selected && admin" title="发布与访问权限" @close="dialog = false">
      <p class="pp-muted">{{ selected.name }}</p>
      <form @submit.prevent="savePublish">
        <div class="pp-modal-body">
          <label class="pp-field">
            <span>允许访问的成员</span>
            <select v-model="publishForm.role">
              <option v-for="(v, k) in packageRoles" :key="k" :value="k">{{ v }}</option>
            </select>
          </label>
          <label class="pp-checkbox">
            <input v-model="publishForm.published" type="checkbox" />
            发布此产出包
          </label>
        </div>
        <footer class="pp-modal-footer">
          <button type="button" class="pp-button" @click="dialog = false">取消</button>
          <button type="submit" class="pp-button primary">保存发布设置</button>
        </footer>
      </form>
    </AppModal>
  </div>
</template>
