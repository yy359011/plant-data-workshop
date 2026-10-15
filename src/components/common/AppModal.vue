<script setup lang="ts">
// 统一弹窗：基于 Element Plus el-dialog，保留原 busy 禁关语义。
// 用内部 visible ref 驱动 el-dialog 完整关闭流程，确保 close 事件能正确触发父组件卸载。
import { onMounted, ref } from 'vue';
import { IconX } from '@tabler/icons-vue';
import AppIcon from './AppIcon.vue';

const props = withDefaults(defineProps<{ title: string; wide?: boolean; busy?: boolean }>(), {
  wide: false,
  busy: false,
});

const emit = defineEmits<{ (e: 'close'): void }>();

const visible = ref(false);

onMounted(() => {
  // 等 DOM-ready 再开，让 el-dialog 走完整 mount 生命周期
  requestAnimationFrame(() => { visible.value = true; });
});

function beforeClose(done: () => void): void {
  if (!props.busy) done();
}
</script>

<template>
  <el-dialog
    v-model="visible"
    :title="title"
    :width="wide ? 'min(880px, calc(100vw - 40px))' : 'min(600px, calc(100vw - 40px))'"
    top="6vh"
    :close-on-click-modal="!busy"
    :close-on-press-escape="!busy"
    :show-close="!busy"
    append-to-body
    :before-close="beforeClose"
    @close="emit('close')"
  >
    <div class="app-modal-body">
      <slot />
    </div>
    <template #footer>
      <slot name="footer" />
    </template>
  </el-dialog>
</template>

<style scoped>
.app-modal-body {
  font-size: 12px;
  line-height: 1.7;
  color: #435a7a;
}
</style>
