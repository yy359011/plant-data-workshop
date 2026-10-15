<script setup lang="ts">
import type { Component } from 'vue';
import AppIcon from './AppIcon.vue';

withDefaults(
  defineProps<{ icon?: Component; variant?: string; className?: string }>(),
  { variant: '', className: '' },
);
</script>

<template>
  <button class="button" :class="[variant, className]">
    <AppIcon v-if="icon" :icon="icon" :size="18" />
    <span v-if="$slots.default"><slot /></span>
  </button>
</template>
