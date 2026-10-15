<script setup lang="ts">
import { ref } from 'vue';
import AppModal from '../common/AppModal.vue';
import AppButton from '../common/AppButton.vue';
import { roleNames } from '../../utils/format';
import { workspace, setToast } from '../../stores/workspace';
import { platformRequest } from '../../api/client';

const props = defineProps<{ user: any }>();
const emit = defineEmits<{ (e: 'close'): void; (e: 'logout'): void }>();

const current = ref('');
const next = ref('');
const busy = ref(false);
const error = ref('');

async function savePassword(): Promise<void> {
  busy.value = true;
  error.value = '';
  try {
    await platformRequest('/api/platform/password', { current_password: current.value, new_password: next.value });
    current.value = '';
    next.value = '';
    setToast('密码已修改，请重新登录');
    emit('logout');
  } catch (e) {
    error.value = e instanceof Error ? e.message : String(e);
  } finally {
    busy.value = false;
  }
}
</script>

<template>
  <AppModal title="我的账号" @close="emit('close')">
    <h3>{{ user.display_name }}</h3>
    <p class="muted">{{ user.username }} · {{ roleNames[user.role] }}</p>
    <details class="ph-pad card">
      <summary>修改我的密码</summary>
      <form class="ph-form-grid" @submit.prevent="savePassword">
        <label>
          当前密码
          <input v-model="current" type="password" required autoComplete="current-password" />
        </label>
        <label>
          新密码
          <input v-model="next" type="password" required minlength="10" autoComplete="new-password" />
        </label>
        <button class="button primary" :disabled="busy">保存新密码</button>
        <div v-if="error" class="ph-error">{{ error }}</div>
      </form>
    </details>
    <div class="app-account-logout">
      <AppButton @click="emit('logout')">退出登录</AppButton>
    </div>
  </AppModal>
</template>

<style scoped>
.app-account-logout {
  margin-top: 16px;
}
</style>
