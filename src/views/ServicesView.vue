<script setup lang="ts">
// 服务配置（对照 App.jsx services 页：ServiceSettings + OCRSettings）
import { onMounted, ref } from 'vue';
import { IconSettings, IconScan } from '@tabler/icons-vue';
import AppIcon from '../components/common/AppIcon.vue';
import AppHeading from '../components/common/AppHeading.vue';
import { platformRequest } from '../api/client';
import { setToast } from '../stores/workspace';

const model = ref({ endpoint: '', model: '', key: '', vision: false, has_key: false });
const ocr = ref({ endpoint: '', key: '', has_key: false, contract: null });
const error = ref('');
const ocrError = ref('');
const busy = ref(false);
const ocrBusy = ref(false);

onMounted(() => {
  platformRequest('/api/chapters/state')
    .then((d) => {
      model.value = { ...d.model, key: '' };
    })
    .catch((e) => (error.value = e instanceof Error ? e.message : String(e)));
  platformRequest('/api/ocr/config')
    .then((d) => {
      ocr.value = { ...d.config, key: '' };
    })
    .catch((e) => (ocrError.value = e instanceof Error ? e.message : String(e)));
});

async function saveModel(): Promise<void> {
  busy.value = true;
  error.value = '';
  try {
    const d = await platformRequest('/api/chapters/model', model.value);
    model.value = { ...d.model, key: '' };
    setToast('模型配置已保存');
  } catch (e) {
    error.value = e instanceof Error ? e.message : String(e);
  } finally {
    busy.value = false;
  }
}

async function saveOcr(): Promise<void> {
  ocrBusy.value = true;
  ocrError.value = '';
  try {
    const d = await platformRequest('/api/ocr/config', {
      endpoint: ocr.value.endpoint,
      ...(ocr.value.key.trim() ? { key: ocr.value.key } : {}),
    });
    ocr.value = { ...d.config, key: '' };
    setToast('OCR 接口配置已保存');
  } catch (e) {
    ocrError.value = e instanceof Error ? e.message : String(e);
  } finally {
    ocrBusy.value = false;
  }
}
</script>

<template>
  <div class="ph-page">
    <div class="ph-section-banner">
      <AppHeading title="服务配置" subtitle="连接章节模型与 OCR 服务。密钥仅保存在本地，不写入操作记录。" />
    </div>

    <section class="card ph-pad">
      <h2><AppIcon :icon="IconSettings" :size="20" />章节模型服务</h2>
      <p class="muted">配置用于章节提炼和字段提取的兼容服务，保存设置不会提交文献。</p>
      <div v-if="error" class="ph-error">{{ error }}</div>
      <form class="ph-form-grid" @submit.prevent="saveModel">
        <label>
          服务基础地址
          <input v-model="model.endpoint" type="url" placeholder="https://example.org/v1" />
        </label>
        <label>
          模型名称
          <input v-model="model.model" />
        </label>
        <label>
          API 密钥
          <input
            v-model="model.key"
            type="password"
            autoComplete="off"
            :placeholder="model.has_key ? '已保存，留空保留' : '未配置'"
          />
        </label>
        <label class="ph-checkbox">
          <input v-model="model.vision" type="checkbox" />
          模型支持图片输入
        </label>
        <button class="button primary" :disabled="busy">保存模型配置</button>
      </form>
    </section>

    <section class="card ph-pad">
      <h2><AppIcon :icon="IconScan" :size="20" />OCR 服务</h2>
      <p class="muted">接入已有文档识别服务。尚未配置时任务保留为"待配置服务"，配置后可从数据资产重试。</p>
      <div v-if="ocrError" class="ph-error">{{ ocrError }}</div>
      <form class="ph-form-grid" @submit.prevent="saveOcr">
        <label>
          识别接口完整地址
          <input v-model="ocr.endpoint" type="url" placeholder="https://example.org/ocr" />
        </label>
        <label>
          API 密钥
          <input
            v-model="ocr.key"
            type="password"
            autoComplete="off"
            :placeholder="ocr.has_key ? '已保存，留空保留' : '未配置'"
          />
        </label>
        <button class="button primary" :disabled="ocrBusy">保存 OCR 配置</button>
      </form>
      <details class="ph-slim-note">
        <summary>接口输入输出约定</summary>
        <pre class="ph-ocr-contract">{{
          typeof ocr.contract === 'string'
            ? ocr.contract
            : JSON.stringify(
                ocr.contract ||
                  {
                    request: { name: '文件名.pdf', content_base64: '原文件的 Base64' },
                    response: { pages: [{ number: 1, markdown: '# 章节标题\n正文' }] },
                  },
                null,
                2,
              )
        }}</pre>
      </details>
    </section>
  </div>
</template>
