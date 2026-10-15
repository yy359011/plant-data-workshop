<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref, watch } from 'vue';
import {
  IconLeaf,
  IconHome,
  IconDatabase,
  IconBook2,
  IconFileDescription,
  IconSearch,
  IconHistory,
  IconBraces,
  IconShieldCheck,
  IconSettings,
  IconChevronRight,
  IconChevronDown,
  IconFolder,
  IconHelpCircle,
  IconAlertTriangle,
  IconLoader2,
  IconX,
  IconUpload,
  IconPlus,
} from '@tabler/icons-vue';
import type { Component } from 'vue';
import { useHashRoute, readHash } from '../composables/useHashRoute';
import { workspace, refreshWorkspace, go, allowedMenu, act } from '../stores/workspace';
import { roleNames } from '../utils/format';
import { api } from '../api/client';
import AppIcon from './common/AppIcon.vue';
import AppButton from './common/AppButton.vue';
import AppEmpty from './common/AppEmpty.vue';
import AppModal from './common/AppModal.vue';
import DashboardView from '../views/DashboardView.vue';
import DataHubView from '../views/DataHubView.vue';
import UsersView from '../views/UsersView.vue';
import ServicesView from '../views/ServicesView.vue';
import OutputCatalogView from '../views/OutputCatalogView.vue';
import KnowledgeSearchView from '../views/KnowledgeSearchView.vue';
import GlobalSearchView from '../views/GlobalSearchView.vue';
import AuditTrailView from '../views/AuditTrailView.vue';
import PromptAdminView from '../views/PromptAdminView.vue';
import LibraryView from '../views/LibraryView.vue';
import BatchWorkspaceView from '../views/BatchWorkspaceView.vue';
import ChapterWorkshopView from '../views/ChapterWorkshopView.vue';
import ImportModal from './modal/ImportModal.vue';
import AssetModal from './modal/AssetModal.vue';
import SettingsModal from './modal/SettingsModal.vue';
import HelpModal from './modal/HelpModal.vue';
import AccountModal from './modal/AccountModal.vue';

const props = defineProps<{ user: any }>();
const emit = defineEmits<{ (e: 'logout'): void }>();

const menuIcons: Record<string, Component> = {
  dashboard: IconHome,
  assets: IconDatabase,
  library: IconBook2,
  batches: IconShieldCheck,
  outputs: IconFileDescription,
  knowledge: IconSearch,
  logs: IconHistory,
  prompts: IconBraces,
  users: IconShieldCheck,
  services: IconSettings,
};

useHashRoute();

const page = computed(() => workspace.page);
const menu = computed(() => allowedMenu(props.user.role));
const pageLabel = computed(() => {
  if (page.value === 'chapters') return '文献管理';
  if (page.value === 'search') return '全局检索';
  return menu.value.find(([k]) => k === page.value)?.[1] || '工作台';
});
const isActive = (key: string) =>
  page.value === key || (key === 'library' && page.value === 'chapters');

function openImport(): void {
  workspace.modal = 'import';
}

function submitSearch(): void {
  if (!workspace.search.trim()) return;
  go((props.user.role === 'external' ? 'knowledge' : 'search') + '?q=' + encodeURIComponent(workspace.search));
}

function abandonAndLeave(): void {
  const target = workspace.leaveTarget;
  if (!target) return;
  workspace.chapterDirty = false;
  workspace.leaveTarget = null;
  go(target);
}

onMounted(() => {
  void refreshWorkspace();
  const onDirty = (e: Event) => {
    workspace.chapterDirty = !!((e as CustomEvent).detail?.dirty);
  };
  window.addEventListener('chapter-unsaved', onDirty);
  onUnmounted(() => window.removeEventListener('chapter-unsaved', onDirty));
});

watch(
  () => [workspace.page, props.user.role],
  () => {
    if (['tasks', 'review'].includes(workspace.page)) go('batches');
    if (workspace.page === 'permissions') go('outputs');
    if (workspace.page === 'chapters' && !readHash().get('document')) go('library');
    if (props.user.role === 'internal' && ['prompts', 'users', 'services'].includes(workspace.page)) go('library');
    if (props.user.role === 'external' && !['outputs', 'knowledge'].includes(workspace.page)) go('outputs');
  },
  { immediate: true },
);
</script>

<template>
  <div class="app-shell">
    <aside class="sidebar">
      <a class="brand" href="#dashboard">
        <span class="brand-icon"><AppIcon :icon="IconLeaf" :size="29" /></span>
        <span>植物科研<span class="brand-light">数据工坊</span></span>
      </a>
      <div class="workspace-label">科研工作空间</div>
      <div class="project-choice">
        <AppIcon :icon="IconFolder" />
        <span>{{ workspace.data.settings.project_name }}</span>
        <AppIcon :icon="IconChevronDown" :size="16" />
      </div>
      <nav>
        <a
          v-for="[key, label] in menu"
          :key="key"
          class="nav-item"
          :class="{ active: isActive(key) }"
          :href="'#' + key"
          :title="label"
          :aria-current="isActive(key) ? 'page' : undefined"
        >
          <AppIcon :icon="menuIcons[key]" />
          <span>{{ label }}</span>
        </a>
        <button class="nav-item" title="帮助中心" @click="workspace.modal = 'help'">
          <AppIcon :icon="IconHelpCircle" /><span>帮助中心</span>
        </button>
        <button v-if="user.role === 'admin'" class="nav-item" title="工作空间设置" @click="workspace.modal = 'settings'">
          <AppIcon :icon="IconSettings" /><span>工作空间设置</span>
        </button>
      </nav>
      <div class="sidebar-bottom">
        <button class="profile-button" title="我的账号" @click="workspace.modal = 'account'">
          <div class="profile"><AppIcon :icon="IconLeaf" :size="18" /></div>
          <span><strong>{{ user.display_name }}</strong><small>{{ roleNames[user.role] }}</small></span>
        </button>
      </div>
    </aside>

    <div class="main-shell">
      <main>
        <div v-if="workspace.error" class="notice error">
          <AppIcon :icon="IconAlertTriangle" />
          <span>{{ workspace.error }}</span>
          <button @click="refreshWorkspace">重新连接</button>
        </div>
        <div v-if="workspace.loading" class="initial-loading">
          <AppIcon :icon="IconLoader2" :size="28" />
          <p>正在载入植物科研资料…</p>
        </div>
        <template v-else>
          <DashboardView v-if="page === 'dashboard'" @import="openImport" />
          <DataHubView v-else-if="page === 'assets'" :user="user" @import="openImport" />
          <LibraryView v-else-if="page === 'library'" @import="openImport" />
          <ChapterWorkshopView v-else-if="page === 'chapters'" :user="user" />
          <BatchWorkspaceView v-else-if="page === 'batches'" :user="user" />
          <OutputCatalogView v-else-if="page === 'outputs'" :user="user" />
          <KnowledgeSearchView v-else-if="page === 'knowledge'" :user="user" />
          <GlobalSearchView v-else-if="page === 'search' && user.role !== 'external'" />
          <AuditTrailView v-else-if="page === 'logs'" />
          <PromptAdminView v-else-if="page === 'prompts' && user.role === 'admin'" :user="user" />
          <UsersView v-else-if="page === 'users' && user.role === 'admin'" :user="user" />
          <ServicesView v-else-if="page === 'services' && user.role === 'admin'" />
          <div v-else class="card">
            <AppEmpty
              title="页面不存在"
              text=""
            >
              <template #action>
                <AppButton @click="go('dashboard')">返回工作台</AppButton>
              </template>
            </AppEmpty>
          </div>
        </template>
        <footer class="page-footer">
          <span>植物科研数据工坊</span>
          <span>本地保存 · 文献、批次与数据包全程可追溯</span>
        </footer>
      </main>
    </div>

    <AppModal
      v-if="workspace.leaveTarget"
      title="还有未保存的修改"
      @close="workspace.leaveTarget = null"
    >
      <p>当前章节的修改尚未保存。可以留在此页继续整理，或放弃本次未保存内容后离开。</p>
      <template #footer>
        <AppButton @click="workspace.leaveTarget = null">继续编辑</AppButton>
        <AppButton @click="abandonAndLeave">放弃修改并离开</AppButton>
      </template>
    </AppModal>

    <AccountModal
      v-if="workspace.modal === 'account'"
      :user="user"
      @close="workspace.modal = null"
      @logout="emit('logout')"
    />
    <ImportModal v-if="workspace.modal === 'import'" @close="workspace.modal = null" />
    <AssetModal v-if="workspace.modal === 'asset' && workspace.asset" @close="workspace.modal = null" />
    <SettingsModal v-if="workspace.modal === 'settings'" @close="workspace.modal = null" />
    <HelpModal v-if="workspace.modal === 'help'" @close="workspace.modal = null" />
  </div>
</template>
