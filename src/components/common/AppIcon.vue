<script setup lang="ts">
// 包装 @tabler/icons-vue 图标：统一 stroke=1.7，与 React 版视觉一致。
import type { Component } from 'vue';

withDefaults(
  defineProps<{ icon?: Component; size?: number; stroke?: number }>(),
  { size: 21, stroke: 1.7 },
);
</script>

<template>
  <component v-if="icon" :is="icon" :size="size" :stroke="stroke" />
</template>
