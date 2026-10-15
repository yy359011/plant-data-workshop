<script setup lang="ts">
// 占位视图：后续功能页完成实现后逐个替换。
import { IconHammer } from '@tabler/icons-vue';
import AppEmpty from '../components/common/AppEmpty.vue';

defineProps<{ name: string }>();
</script>

<template>
  <section class="card ph-pad">
    <AppEmpty :icon="IconHammer" :title="`${name} · 功能页建设中`" text="迁移过程中暂时显示此占位内容。" />
  </section>
</template>
