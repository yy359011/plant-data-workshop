<script setup lang="ts">
// 导入植物科研资料（对照 App.jsx ImportModal）
import { ref } from 'vue';
import { IconUpload, IconFileText } from '@tabler/icons-vue';
import AppModal from '../common/AppModal.vue';
import AppIcon from '../common/AppIcon.vue';
import AppButton from '../common/AppButton.vue';
import AppField from '../common/AppField.vue';
import { workspace, act } from '../../stores/workspace';
import { api } from '../../api/client';
import { purposes, licenses, size } from '../../utils/format';

const emit = defineEmits<{ (e: 'close'): void }>();

const mode = ref<'file' | 'text'>('file');
const files = ref<File[]>([]);
const text = ref('');
const name = ref('植物科研记录.md');
const purpose = ref('both');
const license = ref('unknown');
const process = ref(true);
const error = ref('');

function choose(list: FileList | File[]): void {
  const picked = Array.from(list);
  const total = picked.reduce((sum, f) => sum + f.size, 0);
  if (picked.length > 100 || total > 32 * 1024 * 1024) {
    error.value = '每批最多 100 个文件，总容量不超过 32 MB。';
    return;
  }
  if (picked.some((f) => f.size > 16 * 1024 * 1024)) {
    error.value = '单个文件请控制在 16 MB 以内。';
    return;
  }
  files.value = picked;
  error.value = '';
}

function encode(file: File): Promise<{ name: string; content_base64: string }> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () =>
      resolve({ name: file.name, content_base64: String(reader.result).split(',')[1] });
    reader.onerror = () => reject(Error('无法读取文件'));
    reader.readAsDataURL(file);
  });
}

async function submit(): Promise<void> {
  error.value = '';
  try {
    const batch =
      mode.value === 'text'
        ? [await encode(new File([text.value], name.value || '植物科研记录.md', { type: 'text/plain' }))]
        : await Promise.all(files.value.map(encode));
    await act(
      () => api('/import', { files: batch, purpose: purpose.value, license: license.value, process: process.value }),
      '资料已导入，处理结果已保存。',
    );
    emit('close');
  } catch (e) {
    error.value = e instanceof Error ? e.message : String(e);
  }
}
</script>

<template>
  <AppModal title="导入植物科研资料" :busy="workspace.busy" @close="emit('close')">
    <div class="tabs">
      <button type="button" class="active" @click="mode = 'file'">上传文件</button>
      <button type="button" @click="mode = 'text'">粘贴文本</button>
    </div>

    <template v-if="mode === 'file'">
      <label
        class="dropzone"
        @dragover.prevent
        @drop.prevent="choose($event.dataTransfer!.files)"
      >
        <AppIcon :icon="IconUpload" :size="32" />
        <strong>点击选择或拖入科研资料</strong>
        <span>PDF、TXT、MD、CSV、JSON、JSONL、XLSX、DOCX 和图片</span>
        <small>单文件 ≤ 16 MB · 每批 ≤ 32 MB · 保存在当前电脑</small>
        <input
          type="file"
          multiple
          aria-label="选择科研资料文件"
          accept=".pdf,.txt,.md,.csv,.json,.jsonl,.xlsx,.docx,.png,.jpg,.jpeg,.tif,.tiff,.webp"
          @change="choose(($event.target as HTMLInputElement).files as FileList)"
        />
      </label>
      <ul v-if="files.length" class="selected-files">
        <li v-for="(f, i) in files" :key="i">
          <AppIcon :icon="IconFileText" :size="17" />
          <span>{{ f.name }}</span>
          <small>{{ size(f.size) }}</small>
        </li>
      </ul>
    </template>

    <template v-else>
      <AppField label="文件名称">
        <input v-model="name" required />
      </AppField>
      <AppField label="资料内容">
        <textarea
          v-model="text"
          required
          rows="7"
          placeholder="粘贴植物研究方法、实验记录或文献正文…"
        />
      </AppField>
    </template>

    <div class="dual-grid modal-fields">
      <AppField label="计划用途">
        <select v-model="purpose">
          <option v-for="(v, k) in purposes" :key="k" :value="k">{{ v }}</option>
        </select>
      </AppField>
      <AppField label="授权范围">
        <select v-model="license">
          <option v-for="(v, k) in licenses" :key="k" :value="k">{{ v }}</option>
        </select>
      </AppField>
    </div>

    <div class="small-notice">用途待核对的资料可先解析和复核，确认授权后再用于产出。</div>
    <label class="check-field">
      <input v-model="process" type="checkbox" />
      导入后立即解析并进行质量检查
    </label>
    <p v-if="error" class="form-error" role="alert">{{ error }}</p>

    <template #footer>
      <AppButton type="button" @click="emit('close')">取消</AppButton>
      <AppButton
        variant="primary"
        :icon="IconUpload"
        :disabled="workspace.busy || (mode === 'file' ? !files.length : !text.trim())"
        @click="submit"
      >
        {{ workspace.busy ? '正在导入…' : '导入资料' }}
      </AppButton>
    </template>
  </AppModal>
</template>
