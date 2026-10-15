<script setup lang="ts">
// PDF 原件阅读器（pdfjs-dist v3.11.174，canvas 渲染）
// 主库和 worker 都通过 index.html 的 UMD <script> 标签加载，挂在 window 上。
// 这样完全绕过 Vite 的 CJS→ESM Proxy 转换。
import { nextTick, onMounted, onUnmounted, ref, watch } from 'vue';

const pdfjs = (globalThis as any).pdfjsLib;
const { getDocument, GlobalWorkerOptions } = pdfjs;

// 用真正的 Web Worker（public/ 下的原生文件，不经过 Vite 转换）
// 走 BASE_URL 前缀，兼容部署到子路径（如 GitHub Pages）
const assetBase = import.meta.env.BASE_URL || '/';
GlobalWorkerOptions.workerSrc = assetBase + 'pdfjs/pdf.worker.js';
import {
  IconChevronLeft,
  IconChevronRight,
  IconMinus,
  IconPlus,
  IconExternalLink,
  IconLoader2,
  IconAlertTriangle,
} from '@tabler/icons-vue';
import AppIcon from './common/AppIcon.vue';

const props = defineProps<{ url: string; pageNumber?: number }>();
const emit = defineEmits<{ (e: 'page-change', page: number): void }>();

const pdf = ref<any>(null);
const error = ref('');
const loading = ref(true);
const rendering = ref(false);
const page = ref(Number(props.pageNumber) || 1);
const input = ref(String(props.pageNumber || 1));
const zoom = ref(1);
const width = ref(550);
const description = ref('');
const viewportRef = ref<HTMLElement | null>(null);
const canvasRef = ref<HTMLCanvasElement | null>(null);

let resizeObserver: ResizeObserver | null = null;

onMounted(() => {
  if (viewportRef.value) {
    resizeObserver = new ResizeObserver((entries) => {
      width.value = Math.max(180, entries[0].contentRect.width - 28);
    });
    resizeObserver.observe(viewportRef.value);
  }
});
onUnmounted(() => resizeObserver?.disconnect());

watch(
  () => props.pageNumber,
  (value) => {
    page.value = Number(value) || 1;
    input.value = String(value || 1);
  },
);

watch(
  () => props.url,
  (url) => {
    let active = true;
    pdf.value = null;
    error.value = '';
    loading.value = true;
    description.value = '';
    zoom.value = 1;
    let blobUrl: string | null = null;
    let loadingTask: any = null;
    if (!url) {
      loading.value = false;
      return;
    }
    (async () => {
      // pdf.js 的 worker 里 fetch 不会带 cookie/session（后端鉴权返回 401），
      // 所以主线程先用 credentials: 'include' fetch PDF → blob，
      // 生成 blob: URL 传给 getDocument，Worker fetch blob: URL 同源不需要 cookie。
      const response = await fetch(url, { credentials: 'include' });
      if (!response.ok) throw new Error(`PDF 请求失败 ${response.status}`);
      const blob = await response.blob();
      blobUrl = URL.createObjectURL(blob);
      loadingTask = getDocument({
        url: blobUrl,
        cMapUrl: assetBase + 'pdfjs/cmaps/',
        cMapPacked: true,
        standardFontDataUrl: assetBase + 'pdfjs/standard_fonts/',
        isEvalSupported: false,
        enableXfa: false,
        useSystemFonts: true,
      });
      console.log('[PdfViewer] getDocument called, blobUrl=', blobUrl);
      const document = await loadingTask.promise;
      console.log('[PdfViewer] got doc, numPages=', document.numPages);
      if (active) {
        pdf.value = document;
        loading.value = false;
      }
    })().catch((failure: any) => {
      console.error('[PdfViewer] getDocument failure:', failure?.name, failure?.message, failure);
      if (!active) return;
      error.value =
        failure.name === 'PasswordException'
          ? '这份 PDF 有密码保护，请先上传可阅读的副本。'
          : failure.message?.includes('失败')
            ? failure.message
            : 'PDF 暂时无法显示，请检查文件或打开原件查看。';
      loading.value = false;
    });
    return () => {
      active = false;
      if (blobUrl) URL.revokeObjectURL(blobUrl);
      if (loadingTask) void loadingTask.destroy().catch(() => {});
    };
  },
  { immediate: true },
);

watch([pdf, page, width, zoom], async ([p, pg, w, z]) => {
  if (!p) return;
  let active = true;
  let renderTask: any;
  error.value = '';
  description.value = '';
  if (!Number.isInteger(pg) || pg < 1 || pg > p.numPages) {
    error.value = `对应页码 ${pg} 超出 PDF 的 1–${p.numPages} 页范围，请调整页码偏移或输入正确页码。`;
    rendering.value = false;
    return;
  }
  rendering.value = true;
  await nextTick();
  try {
    const pdfPage = await p.getPage(pg);
    console.log('[PdfViewer] getPage done, page=', pg);
    if (!active) return;
    if (!canvasRef.value) return;
    const canvas = canvasRef.value;
    const base = pdfPage.getViewport({ scale: 1 });
    const scale = Math.min(w / base.width, 1.65) * z;
    const view = pdfPage.getViewport({ scale });
    const pixelRatio = Math.min(window.devicePixelRatio || 1, 2);
    console.log('[PdfViewer] canvas size:', view.width, 'x', view.height, 'scale=', scale);
    canvas.width = Math.ceil(view.width * pixelRatio);
    canvas.height = Math.ceil(view.height * pixelRatio);
    canvas.style.width = `${Math.round(view.width)}px`;
    canvas.style.height = `${Math.round(view.height)}px`;
    renderTask = pdfPage.render({
      canvasContext: canvas.getContext('2d') as CanvasRenderingContext2D,
      viewport: view,
      transform: pixelRatio === 1 ? undefined : [pixelRatio, 0, 0, pixelRatio, 0, 0],
    });
    console.log('[PdfViewer] render task created');
    await renderTask.promise;
    console.log('[PdfViewer] render done');
    if (!active) return;
    description.value = `PDF 第 ${pg} 页，共 ${p.numPages} 页。`;
    rendering.value = false;
  } catch (failure: any) {
    console.error('[PdfViewer] render failure:', failure?.name, failure?.message);
    console.error('[PdfViewer] STACK:', failure?.stack || String(failure));
    if (active && failure.name !== 'RenderingCancelledException') {
      error.value = '本页渲染失败，可切换页面重试或打开原始 PDF。';
      rendering.value = false;
    }
  }
  return () => {
    active = false;
    renderTask?.cancel();
  };
});

function changePage(next: number): void {
  if (!pdf.value || !Number.isInteger(next) || next < 1 || next > pdf.value.numPages) {
    input.value = String(page.value);
    return;
  }
  page.value = next;
  input.value = String(next);
  emit('page-change', next);
}

const inRange = () => !!pdf.value && page.value >= 1 && page.value <= pdf.value.numPages;
</script>

<template>
  <div class="pdf-viewer">
    <div ref="viewportRef" class="pdf-viewport">
      <div v-if="loading" class="pdf-overlay">
        <AppIcon :icon="IconLoader2" :size="28" class="spin" />
        <span>正在加载 PDF…</span>
      </div>
      <div v-else-if="error" class="pdf-overlay pdf-error">
        <AppIcon :icon="IconAlertTriangle" :size="24" />
        <span>{{ error }}</span>
      </div>
      <canvas ref="canvasRef" class="pdf-canvas" />
    </div>
    <div v-if="pdf" class="pdf-toolbar">
      <div class="pdf-pager">
        <button type="button" :disabled="!inRange || page <= 1" @click="changePage(page - 1)" title="上一页">
          <AppIcon :icon="IconChevronLeft" :size="16" />
        </button>
        <input
          :value="input"
          @change="(e) => changePage(Number((e.target as HTMLInputElement).value))"
          type="number"
          min="1"
          :max="pdf.numPages"
          class="pdf-page-input"
          aria-label="页码"
        />
        <span class="pdf-page-total">/ {{ pdf.numPages }}</span>
        <button type="button" :disabled="!inRange || page >= pdf.numPages" @click="changePage(page + 1)" title="下一页">
          <AppIcon :icon="IconChevronRight" :size="16" />
        </button>
      </div>
      <div class="pdf-zoom">
        <button type="button" :disabled="zoom <= 0.5" @click="zoom = Math.max(0.5, +(zoom - 0.25).toFixed(2))" title="缩小">
          <AppIcon :icon="IconMinus" :size="16" />
        </button>
        <span class="pdf-zoom-pct">{{ Math.round(zoom * 100) }}%</span>
        <button type="button" :disabled="zoom >= 3" @click="zoom = Math.min(3, +(zoom + 0.25).toFixed(2))" title="放大">
          <AppIcon :icon="IconPlus" :size="16" />
        </button>
      </div>
      <span v-if="rendering" class="pdf-rendering">渲染中…</span>
      <span v-else-if="description" class="pdf-desc">{{ description }}</span>
      <a :href="url" target="_blank" rel="noopener noreferrer" class="pdf-open" title="在新标签打开原始 PDF">
        <AppIcon :icon="IconExternalLink" :size="16" />
      </a>
    </div>
  </div>
</template>

<style scoped>
.pdf-viewer {
  display: flex;
  flex-direction: column;
  height: 100%;
  min-height: 0;
}
.pdf-viewport {
  flex: 1;
  min-height: 0;
  overflow: auto;
  background: #F3F4F6;
  border-radius: 8px;
  padding: 14px;
  position: relative;
  display: flex;
  justify-content: center;
}
.pdf-canvas {
  display: block;
  box-shadow: 0 1px 3px rgba(16, 24, 40, 0.1);
  background: #fff;
  border-radius: 2px;
}
.pdf-overlay {
  position: absolute;
  inset: 0;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 10px;
  color: #6B7280;
  font-size: 13px;
}
.pdf-overlay.pdf-error { color: #EF4444; }
.spin { animation: spin 1s linear infinite; }
@keyframes spin { to { transform: rotate(360deg); } }

.pdf-toolbar {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 10px 4px 2px;
  font-size: 12px;
  color: #4B5563;
}
.pdf-toolbar button,
.pdf-toolbar a {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 28px;
  height: 28px;
  border: 1px solid #E5E7EB;
  border-radius: 6px;
  background: #fff;
  color: #4B5563;
  cursor: pointer;
  transition: border-color 0.15s, color 0.15s, background 0.15s;
}
.pdf-toolbar button:hover:not(:disabled),
.pdf-toolbar a:hover {
  border-color: #4F6EF7;
  color: #4F6EF7;
  background: #EEF2FF;
}
.pdf-toolbar button:disabled {
  opacity: 0.4;
  cursor: not-allowed;
}
.pdf-pager { display: inline-flex; align-items: center; gap: 6px; }
.pdf-page-input {
  width: 48px;
  height: 28px;
  border: 1px solid #E5E7EB;
  border-radius: 6px;
  text-align: center;
  font-size: 12px;
  color: #1F2937;
  background: #fff;
}
.pdf-page-input:focus {
  outline: none;
  border-color: #4F6EF7;
  box-shadow: 0 0 0 2px rgba(79,110,247,0.12);
}
.pdf-page-total { color: #9CA3AF; }
.pdf-zoom { display: inline-flex; align-items: center; gap: 6px; margin-left: auto; }
.pdf-zoom-pct { min-width: 36px; text-align: center; color: #1F2937; font-variant-numeric: tabular-nums; }
.pdf-desc { color: #6B7280; }
.pdf-rendering { color: #4F6EF7; font-style: italic; }
.pdf-open { text-decoration: none; }
</style>
