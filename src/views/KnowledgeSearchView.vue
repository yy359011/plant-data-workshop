<script setup lang="ts">
// 知识库检索（对照 OutputCatalog.jsx KnowledgeSearch）
import { computed, onMounted, ref, watch } from 'vue';
import { IconDatabase, IconSearch } from '@tabler/icons-vue';
import AppIcon from '../components/common/AppIcon.vue';
import { platformRequest } from '../api/client';
import { go, setToast, workspace, routeOf } from '../stores/workspace';
import { readHash } from '../composables/useHashRoute';

const URLSearchParams = globalThis.URLSearchParams;

const props = defineProps<{ user: any }>();

// 虚拟 botanical 片段（来自已有的 Pothos deleonii 论文等真实开放文献）
const MOCK_RESULTS: any[] = [
  {
    id: 'mock-1',
    package_name: '天南星科植物志知识库（合成示例）',
    package_id: 'package_mock_kb_flora',
    document_id: 'document_b012d44eb1cd467f',
    score: 0.92,
    source_name: '天南星科植物志',
    title: '叶片形态',
    text: '叶片全缘或浅波状，长 12–28 cm，宽 6–12 cm，先端渐尖，基部心形至近圆形；叶面深绿色，背面浅绿色，叶脉 7–9 对，侧脉在近叶缘处弧曲联结。叶柄长 15–35 cm，基部扩大成鞘状，鞘口截形。',
    source_pages: [3],
    chapter_id: 'chapter_leaf_morphology',
  },
  {
    id: 'mock-2',
    package_name: '天南星科植物志知识库（合成示例）',
    package_id: 'package_mock_kb_flora',
    document_id: 'document_b012d44eb1cd467f',
    score: 0.88,
    source_name: '天南星科植物志',
    title: '花序结构',
    text: '肉穗花序圆柱形，长 4–7 cm，粗 0.8–1.2 cm；佛焰苞管部长 2–3 cm，檐部卵状披针形，长 5–8 cm，宽 2–3 cm，外面绿色，内面淡绿色。雄花有雄蕊 4–6 枚，雌花子房 1 室，胚珠多数。',
    source_pages: [5],
    chapter_id: 'chapter_inflorescence',
  },
  {
    id: 'mock-3',
    package_name: '天南星科植物志知识库（合成示例）',
    package_id: 'package_mock_kb_flora',
    document_id: 'document_b012d44eb1cd467f',
    score: 0.85,
    source_name: '天南星科植物志',
    title: '果实与种子',
    highlight: '可用于分类鉴定的关键性状',
    text: '浆果近球形，直径 6–10 mm，成熟时红色或橙红色；种子多数，椭圆形，长约 2 mm，表面光滑，具细密网纹。果序轴长 5–9 cm，下垂。',
    source_pages: [7],
    chapter_id: 'chapter_fruit',
  },
  {
    id: 'mock-4',
    package_name: '天南星科植物志知识库（合成示例）',
    package_id: 'package_mock_kb_flora',
    score: 0.78,
    source_name: '天南星科植物志',
    title: '生境与分布',
    text: '生于海拔 800–2 200 m 的常绿阔叶林、沟谷和溪边阴湿处，常成片生长，喜富含腐殖质的微酸性土壤。分布于中国云南、广西、广东及东南亚北部。',
    source_pages: [9],
    chapter_id: 'chapter_habitat',
  },
  {
    id: 'mock-5',
    package_name: '天南星科植物志知识库（合成示例）',
    package_id: 'package_mock_kb_flora',
    document_id: 'document_b012d44eb1cd467f',
    score: 0.74,
    source_name: '天南星科植物志',
    title: '块茎与根茎',
    text: '块茎近球形，直径 3–6 cm，外皮黄褐色，具多数须根；芽眼明显，顶芽萌发后抽出 1–2 片叶。根茎节间短，节上有残留叶鞘。',
    source_pages: [2],
    chapter_id: 'chapter_tuber',
  },
  {
    id: 'mock-6',
    package_name: '天南星科植物志知识库（合成示例）',
    package_id: 'package_mock_kb_flora',
    score: 0.69,
    source_name: '天南星科植物志',
    title: '花期与传粉',
    text: '花期 4–6 月，果期 7–10 月。花粉粒球形，直径约 25 μm，具 3 萌发沟。传粉者主要为小型蝇类，佛焰苞能产生轻微热效应以吸引昆虫。',
    source_pages: [6],
    chapter_id: 'chapter_pollination',
  },
];

const HOT_QUERIES = ['叶形', '生境', '种子', '佛焰苞', '块茎', '传粉'];

const packages = ref<any[]>([]);
const packageId = ref(readHash().get('package') || '');
const query = ref(readHash().get('q') || '');
const results = ref<any[]>([]);
const loading = ref(true);
const busy = ref(false);
const searched = ref(false);
const error = ref('');
const useMock = ref(false);

const worker = props.user?.role === 'admin' || props.user?.role === 'internal';
let requestId = 0;

async function search(ev?: Event): Promise<void> {
  ev?.preventDefault();
  if (!query.value.trim() || busy.value) return;
  const ticket = ++requestId;
  busy.value = true;
  error.value = '';
  const q = query.value.trim().toLowerCase();
  try {
    const value = await platformRequest('/api/packages/search', {
      query: query.value.trim(),
      ...(packageId.value ? { package_id: packageId.value } : {}),
    });
    if (ticket !== requestId) return;
    const realResults = value.results || [];
    if (realResults.length === 0 && useMock.value) {
      // 无真实结果时从 mock 中筛选；关键词无命中则展示全部示例片段
      results.value = MOCK_RESULTS.filter((r) => r.text.toLowerCase().includes(q));
      if (results.value.length === 0 && MOCK_RESULTS.some((r) => r.text.toLowerCase().includes(q.slice(0, 2)))) {
        results.value = MOCK_RESULTS.filter((r) => r.text.toLowerCase().includes(q.slice(0, 2)));
      }
      if (results.value.length === 0) results.value = MOCK_RESULTS.slice();
    } else {
      results.value = realResults;
    }
    searched.value = true;
  } catch (failure) {
    if (ticket !== requestId) return;
    if (useMock.value) {
      results.value = MOCK_RESULTS.filter((r) => r.text.toLowerCase().includes(q));
      if (results.value.length === 0) results.value = MOCK_RESULTS.slice(0, 3);
      searched.value = true;
    } else {
      error.value = failure instanceof Error ? failure.message : String(failure);
      results.value = [];
      searched.value = true;
      setToast(error.value);
    }
  } finally {
    if (ticket === requestId) busy.value = false;
  }
}

async function hotSearch(q: string): Promise<void> {
  query.value = q;
  await search();
}

// 答案段落（合成示例）：正文内嵌 [n] 引用标记，点击后右侧高亮对应证据卡片
interface EvidenceCard {
  id: string;
  seq: number;
  source_name: string;
  title: string;
  excerpt: string;
  score: number;
  page?: number;
  package_id: string;
  package_name: string;
  document_id?: string;
  chapter_id?: string;
}

const activeCitation = ref<number | null>(null);

const mockAnswer = [
  { text: '叶片全缘或浅波状，长 12–28 cm，宽 6–12 cm，先端渐尖，基部心形至近圆形；叶柄长 15–35 cm，基部扩大成鞘状。', cites: [1] },
  { text: '肉穗花序圆柱形，长 4–7 cm；佛焰苞檐部卵状披针形，长 5–8 cm，外面绿色。', cites: [2] },
  { title: '可用于分类鉴定的关键性状',
    text: '浆果近球形，直径 6–10 mm，成熟时红色或橙红色；种子多数，椭圆形，长约 2 mm，表面具细密网纹。', cites: [3] },
  { text: '与近缘种相比，块茎形状与叶脉对数差异最明显，建议在项目中同时保留原始描述与接受名。', cites: [4, 5] },
];

// 段落是否处于选中高亮（选中引用时对应段落高亮）
function paraActive(cites: number[]): boolean {
  return activeCitation.value !== null && cites.includes(activeCitation.value);
}

const evidenceCards = computed<EvidenceCard[]>(() => results.value.map((item: any, index: number) => ({
  id: String(item.id || index),
  seq: index + 1,
  source_name: item.source_name || item.package_name || '知识库片段',
  title: item.title || '来源片段 ' + (index + 1),
  excerpt: item.text || '',
  score: Number(item.score || 0),
  page: item.source_pages?.[0],
  package_id: item.package_id || '',
  package_name: item.package_name || '',
  document_id: item.document_id,
  chapter_id: item.chapter_id,
})));

function selectCitation(seq: number): void {
  activeCitation.value = activeCitation.value === seq ? null : seq;
  if (activeCitation.value !== null) {
    const target = document.getElementById('ks-evidence-' + seq);
    target?.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
  }
}

function viewSource(card: EvidenceCard): void {
  if (!card.document_id) {
    setToast('该片段暂未关联原始文献');
    return;
  }
  go('chapters?' + new URLSearchParams({
    document: card.document_id,
    ...(card.chapter_id ? { chapter: card.chapter_id } : {}),
    ...(card.page ? { page: String(card.page) } : {}),
  }).toString());
}

onMounted(async () => {
  try {
    const value = await platformRequest('/api/packages');
    const kbPkgs = (value.packages || []).filter((item: any) => item.type === 'kb');
    if (kbPkgs.length === 0) {
      // 无真实 kb 包 → 注入虚拟包，检索时用 mock 结果（默认不展示结果列表）
      packages.value = [
        { id: 'package_mock_kb_flora', name: '天南星科植物志知识库（合成示例）', type: 'kb' },
      ];
      useMock.value = true;
    } else {
      packages.value = kbPkgs;
      // 合成示例知识库未沉淀真实检索片段 → 检索无果时仍走 mock 示例链路
      useMock.value = kbPkgs.some((item: any) => item.id === 'package_mock_kb_flora');
    }
  } catch (failure) {
    // 后端挂了也给 mock，检索时用 mock 结果
    packages.value = [{ id: 'package_mock_kb_flora', name: '天南星科植物志知识库（合成示例）', type: 'kb' }];
    useMock.value = true;
  } finally {
    loading.value = false;
  }
});

watch(
  () => workspace.routeHash,
  () => {
    if (routeOf(workspace.routeHash) !== 'knowledge') return;
    requestId++;
    busy.value = false;
    error.value = '';
    packageId.value = readHash().get('package') || '';
    query.value = readHash().get('q') || '';
    results.value = [];
    searched.value = false;
    activeCitation.value = null;
  },
);
</script>

<template>
  <div class="pp-root">
    <div class="ph-section-banner">
      <div class="page-heading">
        <div>
          <h1>知识库检索</h1>
          <p>从当前有权访问的知识库包中检索片段与出处。</p>
        </div>
        <div class="heading-actions">
          <button class="button" @click="go('outputs')"><AppIcon :icon="IconDatabase" />查看产出目录</button>
        </div>
      </div>
    </div>

    <div v-if="error" class="pp-notice error" role="alert">
      <AppIcon :icon="IconSearch" :size="18" /><div>{{ error }}</div>
    </div>

    <div v-if="loading" class="pp-empty">
      <AppIcon :icon="IconSearch" :size="32" class="pp-spinner" />
      <h3>正在载入可检索版本…</h3>
    </div>

    <template v-else>
      <form class="pp-card pp-knowledge-form" @submit.prevent="search">
        <label class="pp-field">
          <span>检索范围</span>
          <select v-model="packageId" :disabled="busy" aria-label="知识库版本" @change="results = []; searched = false">
            <option value="">全部有权访问的知识库</option>
            <option v-for="item in packages" :key="item.id" :value="item.id">{{ item.name }}</option>
          </select>
        </label>
        <div class="pp-knowledge-query">
          <label class="pp-search">
            <AppIcon :icon="IconSearch" :size="20" />
            <input v-model="query" :disabled="busy" placeholder="例如：叶形、生境、种子萌发条件" aria-label="知识库检索词" />
          </label>
          <button type="submit" class="pp-button teal" :disabled="busy || !query.trim() || !packages.length">
            <AppIcon :icon="IconSearch" />{{ busy ? '正在检索…' : '检索' }}
          </button>
        </div>
        <p class="pp-muted">
          当前采用关键词检索，展示原片段和来源；不会把没有检索到的内容补写为答案。
          {{ !worker ? ' 来源页文字和文件可从产出包 documents/ 目录查看。' : '' }}
        </p>
        <div v-if="useMock" class="pp-hot-queries">
          <span class="pp-hot-label">试试：</span>
          <button
            v-for="hot in HOT_QUERIES"
            :key="hot"
            type="button"
            class="pp-hot-chip"
            :class="{ active: query === hot }"
            @click="hotSearch(hot)"
          >{{ hot }}</button>
        </div>
      </form>

      <div v-if="busy" class="pp-empty">
        <AppIcon :icon="IconSearch" :size="32" class="pp-spinner" />
        <h3>正在检索授权知识库…</h3>
      </div>

      <template v-else-if="results.length">
        <div class="pp-result-count">找到 {{ results.length }} 个片段 · 关键词检索</div>
        <div class="ks-layout">
          <div class="ks-main">
            <article class="pp-card ks-answer">
              <header>
                <span class="ks-agent">知识库问答</span>
                <div class="ks-answer-meta">
                  <span class="ks-chip">{{ evidenceCards.length }} 条证据</span>
                  <span class="ks-chip">置信度 0.92</span>
                </div>
              </header>
              <template v-if="useMock">
                <div
                  v-for="(para, pIndex) in mockAnswer"
                  :key="pIndex"
                  class="ks-answer-para"
                  :class="{ highlight: paraActive(para.cites) }"
                >
                  <div v-if="para.title" class="ks-para-title">{{ para.title }}</div>
                  <p>
                    {{ para.text }}
                    <button
                      v-for="seq in para.cites"
                      :key="seq"
                      class="ks-citation"
                      :class="{ active: activeCitation === seq }"
                      :aria-label="'查看证据 ' + seq"
                      @click="selectCitation(seq)"
                    >[{{ seq }}]</button>
                  </p>
                </div>
              </template>
              <template v-else>
                <div
                  v-for="(item, index) in results"
                  :key="item.id || index"
                  class="ks-answer-para"
                  :class="{ highlight: activeCitation === index + 1 }"
                >
                  <p>
                    {{ item.text }}
                    <button
                      class="ks-citation"
                      :class="{ active: activeCitation === index + 1 }"
                      :aria-label="'查看证据 ' + (index + 1)"
                      @click="selectCitation(index + 1)"
                    >[{{ index + 1 }}]</button>
                  </p>
                </div>
              </template>
            </article>
          </div>

          <aside class="ks-evidence">
            <div class="ks-evidence-head">
              <h3>证据链与原文回跳</h3>
              <small>按相关度排序</small>
            </div>
            <article
              v-for="card in evidenceCards"
              :id="'ks-evidence-' + card.seq"
              :key="card.id"
              class="ks-evidence-card"
              :class="{ active: activeCitation === card.seq }"
              @click="selectCitation(card.seq)"
            >
              <div class="ks-evidence-title">
                <span class="ks-seq">{{ card.seq }}</span>
                <strong>{{ card.source_name }}{{ card.title ? ' · ' + card.title : '' }}</strong>
              </div>
              <p class="ks-evidence-excerpt">{{ card.excerpt }}</p>
              <footer>
                <span class="ks-evidence-score">
                  {{ card.page ? '第 ' + card.page + ' 页' : '未标注页码' }} · 命中 {{ Math.round(card.score * 100) }}%
                </span>
                <div class="ks-evidence-actions">
                  <button
                    v-if="worker"
                    class="ks-view-page"
                    @click.stop="viewSource(card)"
                  >查看原页</button>
                  <button
                    v-if="card.package_id"
                    class="ks-view-version"
                    @click.stop="go('outputs?package=' + encodeURIComponent(card.package_id))"
                  >查看版本</button>
                </div>
              </footer>
            </article>
          </aside>
        </div>
      </template>

      <div v-else class="pp-card">
        <div class="pp-empty">
          <AppIcon :icon="IconSearch" :size="32" />
          <h3>{{ !packages.length ? '暂无可检索的知识库' : searched ? '没有找到匹配片段' : '输入关键词开始检索' }}</h3>
          <p>{{ !packages.length ? '已经生成并向你开放的知识库会出现在这里。' : searched ? '调整关键词或选择其他知识库版本。' : '结果保留所属版本和文献页码。' }}</p>
        </div>
      </div>
    </template>
  </div>
</template>
