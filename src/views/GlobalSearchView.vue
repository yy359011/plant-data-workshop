<script setup lang="ts">
// 全局资料检索（对照 GlobalSearch.jsx GlobalSearch）
import { onMounted, ref } from 'vue';
import { IconArrowRight, IconBook2, IconSearch } from '@tabler/icons-vue';
import AppIcon from '../components/common/AppIcon.vue';
import { platformRequest } from '../api/client';
import { go, viewAsset } from '../stores/workspace';
import { readHash } from '../composables/useHashRoute';

const URLSearchParams = globalThis.URLSearchParams;

const initial = readHash().get('q') || '';
const query = ref(initial);
const result = ref<any>(null);
const error = ref('');
const busy = ref(false);
const searched = ref('');

let requestSeq = 0;

function highlight(text: string): string {
  const words = searched.value.trim().split(/\s+/).filter(Boolean);
  if (!words.length || !text) return text;
  const pattern = new RegExp(
    '(' + words.map((w) => w.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')).join('|') + ')',
    'gi',
  );
  return String(text)
    .split(pattern)
    .map((v) => (words.some((w) => w.toLowerCase() === v.toLowerCase()) ? `<mark>${v}</mark>` : v))
    .join('');
}

async function runSearch(q: string): Promise<void> {
  if (!q.trim()) return;
  const seq = ++requestSeq;
  busy.value = true;
  error.value = '';
  try {
    const data = await platformRequest('/api/search', { query: q });
    if (seq === requestSeq) {
      result.value = data;
      searched.value = q;
    }
  } catch (e) {
    if (seq === requestSeq) error.value = e instanceof Error ? e.message : String(e);
  } finally {
    if (seq === requestSeq) busy.value = false;
  }
}

onMounted(() => void runSearch(initial));

function submit(event: Event): void {
  event.preventDefault();
  if (query.value === initial) void runSearch(query.value);
  else go('search?q=' + encodeURIComponent(query.value));
}

const kindLabels: Record<string, string> = {
  literature: '文献信息',
  markdown: '资料文本',
  page: '逐页 Markdown',
  chapter: '章节语料',
  asset: '资料文本',
  document: '文献信息',
};

const rows = () => result.value?.results || [];
</script>

<template>
  <div class="ph-page">
    <div class="ph-section-banner">
      <div class="page-heading">
        <div>
          <h1>全局资料检索</h1>
          <p>搜索物种、性状或研究主题，查看命中的文献与 Markdown，追溯原页和数据包。</p>
        </div>
      </div>
    </div>

    <section class="card ph-pad">
      <form class="ph-search-form" @submit.prevent="submit">
        <input v-model="query" aria-label="全局检索关键词" placeholder="例如 Pothos、叶形、干旱响应" />
        <button class="button primary" :disabled="busy || !query.trim()">
          <AppIcon :icon="IconSearch" :size="18" />{{ busy ? '检索中…' : '搜索' }}
        </button>
      </form>
    </section>

    <div v-if="error" class="ph-error">{{ error }}</div>

    <section v-if="result" class="card ph-pad">
      <div class="section-heading">
        <h2>匹配内容</h2>
        <span class="muted">{{ result.total ?? rows().length }} 条结果</span>
      </div>
      <template v-if="rows().length">
        <article v-for="(r, i) in rows()" :key="r.id || i" class="ph-result">
          <h3 v-html="highlight(String(r.title || r.name || r.document_title || r.asset_name || '').replace(/[（(](?:真实论文|章节管线合成示例|合成演示|测试文献)[）)]/g, '').trim())"></h3>
          <p v-html="highlight(r.snippet || r.text || r.excerpt || '')"></p>
          <div class="ph-result-meta">
            <span>{{ r.kind_label || kindLabels[r.kind] || r.kind }}</span>
            <span v-if="r.page || r.page_number">第 {{ r.page || r.page_number }} 页</span>
            <button
              v-if="r.document_id"
              class="text-button"
              @click="go('chapters?' + new URLSearchParams({ document: r.document_id, ...(r.page || r.page_number ? { page: String(r.page || r.page_number) } : {}), ...(r.chapter_id ? { chapter: r.chapter_id } : {}) }).toString())"
            >
              对照原文<AppIcon :icon="IconArrowRight" :size="14" />
            </button>
            <button v-if="r.asset_id" class="text-button" @click="viewAsset(r.asset_id)">查看原始资料</button>
            <button v-for="id in (r.package_ids || [])" :key="id" class="text-button" @click="go('outputs?package=' + id)">来源数据包</button>
          </div>
        </article>
      </template>
      <div v-else class="ph-empty">
        <strong>没有匹配的内容</strong>
        <p>试试学名、中文名，或更短的性状关键词。</p>
      </div>
    </section>

    <section v-if="!result && !busy" class="card ph-empty">
      <AppIcon :icon="IconBook2" :size="32" />
      <p>从文献、逐页 Markdown 与章节整理结果中查找证据。</p>
    </section>
  </div>
</template>
