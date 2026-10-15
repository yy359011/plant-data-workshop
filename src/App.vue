<script setup lang="ts">
import { onMounted, onUnmounted, reactive } from 'vue';
import { platformRequest, onSessionExpired } from './api/client';
import type { User } from './types/api';
import AuthView from './views/AuthView.vue';
import AppShell from './components/AppShell.vue';

const state = reactive({
  loading: true,
  setup: false,
  localSetupAllowed: true,
  error: '',
  busy: false,
  user: null as User | null,
});

let stopListening: (() => void) | undefined;

async function load(): Promise<void> {
  state.loading = true;
  try {
    const bootstrap = await platformRequest('/api/platform/bootstrap');
    state.setup = bootstrap.needs_setup;
    state.localSetupAllowed = bootstrap.local_setup_allowed !== false;
    if (!bootstrap.needs_setup) {
      const me = await platformRequest('/api/platform/me');
      state.user = me.user;
    }
  } catch (error) {
    const message = error instanceof Error ? error.message : String(error);
    if (!/登录|login|身份|会话/i.test(message)) state.error = message;
  } finally {
    state.loading = false;
  }
}

async function submitAuth(form: { username: string; password: string; display_name: string }): Promise<void> {
  state.busy = true;
  state.error = '';
  try {
    const data = await platformRequest('/api/platform/' + (state.setup ? 'setup' : 'login'), form);
    state.user = data.user || (await platformRequest('/api/platform/me')).user;
    state.setup = false;
  } catch (error) {
    state.error = error instanceof Error ? error.message : String(error);
  } finally {
    state.busy = false;
  }
}

async function logout(): Promise<void> {
  try {
    await platformRequest('/api/platform/logout', {});
  } finally {
    state.user = null;
  }
}

onMounted(() => {
  void load();
  stopListening = onSessionExpired(() => {
    state.user = null;
  });
});
onUnmounted(() => stopListening?.());
</script>

<template>
  <div v-if="state.loading" class="auth-loading">正在连接科研工作空间…</div>
  <AuthView
    v-else-if="!state.user"
    :setup="state.setup"
    :local-setup-allowed="state.localSetupAllowed"
    :error="state.error"
    :busy="state.busy"
    @submit="submitAuth"
    @reload="load"
  />
  <AppShell v-else :user="state.user" @logout="logout" />
</template>
