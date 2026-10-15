<script setup lang="ts">
import { ref } from 'vue';
import { IconBook2, IconExternalLink, IconArrowRight } from '@tabler/icons-vue';
import AppModal from '../common/AppModal.vue';
import AppIcon from '../common/AppIcon.vue';
import AppButton from '../common/AppButton.vue';
import AppBadge from '../common/AppBadge.vue';
import AppEmpty from '../common/AppEmpty.vue';
import { workspace, act, go } from '../../stores/workspace';
import { api } from '../../api/client';
import { purposes, licenses, loc, assetLabel } from '../../utils/format';

const emit = defineEmits<{ (e: 'close'): void }>();

const tab = ref('blocks');
const saving = ref(false);
const item = workspace.asset;

async function saveUse(): Promise<void> {
  saving.value = true;
  try {
    await act(() => api('/assets/' + item.asset.id, { purpose: item.asset.purpose, license: item.asset.license }), '资料用途已保存。');
  } finally {
    saving.value = false;
  }
}
</script>

<template>
  <AppModal v-if="item" :title="assetLabel(item.asset)" wide @close="emit('close')">
    <div class="asset-detail-meta">
      <AppBadge :status="item.asset.status" />
      <span>{{ purposes[item.asset.purpose] }}</span>
      <span>{{ licenses[item.asset.license] }}</span>
      <span>{{ item.asset.source_document_id ? '章节加工语料' : '本地导入' }}</span>
      <a class="text-button push-right" :href="'/api/files/' + item.asset.id" target="_blank" rel="noreferrer">
        查看原文件<AppIcon :icon="IconExternalLink" :size="15" />
      </a>
    </div>

    <div v-if="item.asset.source_document_id" class="notice">
      <AppIcon :icon="IconBook2" />
      <span>此资料由已审核章节语料生成，保留原文献、章节及页码来源。</span>
      <a
        class="text-button"
        :href="
          '#chapters?document=' +
          encodeURIComponent(item.asset.source_document_id) +
          '&page=' +
          encodeURIComponent(item.asset.provenance?.source_pages?.[0] || '')
        "
        @click="emit('close')"
      >
        查看原页与图表<AppIcon :icon="IconArrowRight" :size="15" />
      </a>
    </div>

    <details v-if="item.asset.provenance" class="small-notice">
      <summary>加工来源与版本记录</summary>
      <pre>{{ JSON.stringify(item.asset.provenance, null, 2) }}</pre>
    </details>
    <div v-if="item.asset.error" class="small-notice">{{ item.asset.error }}</div>

    <details class="small-notice">
      <summary>资料用途与授权</summary>
      <div class="ph-form-grid">
        <label>
          用途
          <select v-model="item.asset.purpose">
            <option v-for="(v, k) in purposes" :key="k" :value="k">{{ v }}</option>
          </select>
        </label>
        <label>
          授权
          <select v-model="item.asset.license">
            <option v-for="(v, k) in licenses" :key="k" :value="k">{{ v }}</option>
          </select>
        </label>
        <button class="button" :disabled="saving" @click="saveUse">保存用途</button>
      </div>
    </details>

    <div class="tabs">
      <button :class="{ active: tab === 'blocks' }" @click="tab = 'blocks'">规范化记录（{{ item.blocks.length }}）</button>
      <button :class="{ active: tab === 'raw' }" @click="tab = 'raw'">原始文本</button>
    </div>

    <pre v-if="tab === 'raw'" class="raw-text">{{
      item.raw_text || '该文件暂没有可读取的文字内容。图片与扫描 PDF 需要接入 OCR 后识别。'
    }}</pre>
    <div v-else class="blocks-list">
      <article v-for="(b, i) in item.blocks" :key="b.id">
        <header>
          <span>记录 {{ i + 1 }} · {{ loc(b.location) }}</span>
          <AppBadge :status="b.status" />
        </header>
        <pre>{{ b.text }}</pre>
      </article>
      <AppEmpty v-if="!item.blocks.length" title="暂无规范化记录" text="资料尚未解析，或暂未提取到有效内容。" />
    </div>
  </AppModal>
</template>
