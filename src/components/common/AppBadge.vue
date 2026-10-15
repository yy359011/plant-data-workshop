<script setup lang="ts">
import { statuses } from '../../utils/format';

defineProps<{ status?: string }>();
</script>

<template>
  <span class="badge" :class="status || ''">
    <span class="status-dot" />
    <slot>{{ status ? statuses[status as string] || status : status }}</slot>
  </span>
</template>
