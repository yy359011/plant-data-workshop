<script setup lang="ts">
import type { Component } from 'vue';
import { IconInbox } from '@tabler/icons-vue';
import AppIcon from './AppIcon.vue';

withDefaults(defineProps<{ title?: string; text?: string; icon?: Component }>(), {
  title: '暂无数据',
  icon: IconInbox,
});
</script>

<template>
  <div class="empty">
    <AppIcon :icon="icon" :size="34" />
    <strong>{{ title }}</strong>
    <p v-if="text">{{ text }}</p>
    <slot name="action" />
  </div>
</template>
