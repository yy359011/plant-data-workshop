<script setup lang="ts">
import { ref, computed, onMounted } from 'vue';
import {
  IconDeviceDesktop,
  IconPlus,
  IconTrash,
  IconCheck,
  IconLoader2,
  IconAlertTriangle,
  IconBuildingWarehouse,
} from '@tabler/icons-vue';
import AppModal from '../common/AppModal.vue';
import AppIcon from '../common/AppIcon.vue';
import AppButton from '../common/AppButton.vue';
import AppField from '../common/AppField.vue';
import { workspace, act, refreshWorkspace, setToast } from '../../stores/workspace';
import { api, apiPost, apiDelete } from '../../api/client';
import type { WorkspaceListItem } from '../../types/api';

const emit = defineEmits<{ (e: 'close'): void }>();

// === 工作空间数据 ===
const workspaces = ref<WorkspaceListItem[]>([]);
const loading = ref(false);
const name = ref(workspace.data.settings.project_name);
const error = ref('');
const busy = ref(false);

// === 新增工作空间 ===
const showAddForm = ref(false);
const newWsName = ref('');
const addError = ref('');

// === 删除确认 ===
const deletingId = ref<string | null>(null);

async function loadWorkspaces(): Promise<void> {
  loading.value = true;
  try {
    const r = await api<{ workspaces: WorkspaceListItem[]; active: string }>('/workspaces');
    workspaces.value = r.workspaces || [];
  } catch (e) {
    console.error('load workspaces failed', e);
  } finally {
    loading.value = false;
  }
}

onMounted(loadWorkspaces);

async function saveSettings(): Promise<void> {
  error.value = '';
  busy.value = true;
  try {
    await act(() => api('/settings', { project_name: name.value }), '工作空间设置已保存。');
  } catch (e) {
    error.value = e instanceof Error ? e.message : String(e);
  } finally {
    busy.value = false;
  }
}

async function createWorkspace(): Promise<void> {
  addError.value = '';
  const nm = newWsName.value.trim();
  if (!nm) {
    addError.value = '名称不能为空';
    return;
  }
  busy.value = true;
  try {
    await api('/workspaces', { name: nm });
    setToast(`工作空间「${nm}」已创建`);
    newWsName.value = '';
    showAddForm.value = false;
    await loadWorkspaces();
  } catch (e) {
    addError.value = e instanceof Error ? e.message : String(e);
  } finally {
    busy.value = false;
  }
}

async function activateWorkspace(ws: WorkspaceListItem): Promise<void> {
  if (ws.active) return;
  busy.value = true;
  try {
    await apiPost(`/workspaces/${ws.id}/activate`);
    setToast(`已切换到「${ws.name}」，正在加载数据…`);
    await refreshWorkspace();
    await loadWorkspaces();
    emit('close');
  } catch (e) {
    error.value = e instanceof Error ? e.message : String(e);
  } finally {
    busy.value = false;
  }
}

async function deleteWorkspace(ws: WorkspaceListItem): Promise<void> {
  if (ws.active) {
    error.value = '当前正在使用的工作空间不能删除，请先切换。';
    return;
  }
  if (!confirm(`确定删除工作空间「${ws.name}」？该工作空间的所有数据将被永久删除，无法恢复。`)) return;
  deletingId.value = ws.id;
  busy.value = true;
  try {
    await apiDelete(`/workspaces/${ws.id}`);
    setToast(`工作空间「${ws.name}」已删除`);
    await loadWorkspaces();
  } catch (e) {
    error.value = e instanceof Error ? e.message : String(e);
  } finally {
    deletingId.value = null;
    busy.value = false;
  }
}

function formatDate(iso: string): string {
  if (!iso) return '';
  try {
    const d = new Date(iso);
    return d.toLocaleDateString('zh-CN', { year: 'numeric', month: '2-digit', day: '2-digit' });
  } catch { return iso; }
}
</script>

<template>
  <AppModal title="工作空间设置" :busy="workspace.busy" @close="emit('close')" size="lg">
    <!-- 项目名称 -->
    <section class="settings-section">
      <h3 class="settings-h">项目名称</h3>
      <AppField label="当前项目名称">
        <input v-model="name" required maxlength="60" />
      </AppField>
      <div class="settings-note">
        <AppIcon :icon="IconDeviceDesktop" :size="20" />
        <span>本地运行与保存 — 所有原始文件、数据库与导出版本保存在磁盘上。</span>
      </div>
    </section>

    <!-- 工作空间管理 -->
    <section class="settings-section">
      <header class="ws-header">
        <h3 class="settings-h">
          <AppIcon :icon="IconBuildingWarehouse" :size="20" />
          工作空间（{{ workspaces.length }}）
        </h3>
        <AppButton size="sm" :busy="busy" @click="showAddForm = !showAddForm">
          <AppIcon :icon="showAddForm ? IconLoader2 : IconPlus" :size="16" />
          {{ showAddForm ? '取消' : '新建工作空间' }}
        </AppButton>
      </header>

      <!-- 新增表单 -->
      <div v-if="showAddForm" class="ws-add">
        <AppField label="工作空间名称">
          <input v-model="newWsName" placeholder="例如：XX 课题组数据" maxlength="40" />
        </AppField>
        <p v-if="addError" class="form-error">{{ addError }}</p>
        <AppButton variant="primary" size="sm" :busy="busy || !newWsName.trim()" @click="createWorkspace">
          创建
        </AppButton>
      </div>

      <!-- 工作空间列表 -->
      <div v-if="loading" class="ws-loading">
        <AppIcon :icon="IconLoader2" :size="20" class="spin" /> 加载中…
      </div>
      <div v-else-if="workspaces.length === 0" class="ws-empty">
        暂无工作空间。
      </div>
      <ul v-else class="ws-list">
        <li
          v-for="ws in workspaces"
          :key="ws.id"
          class="ws-item"
          :class="{ 'ws-item--active': ws.active }"
        >
          <div class="ws-item-main">
            <div class="ws-item-name">
              {{ ws.name }}
              <span v-if="ws.legacy" class="ws-badge ws-badge--legacy">历史数据</span>
              <span v-if="ws.active" class="ws-badge ws-badge--active">
                <AppIcon :icon="IconCheck" :size="12" /> 当前
              </span>
            </div>
            <div class="ws-item-meta">
              创建于 {{ formatDate(ws.created_at) }} · ID {{ ws.id.slice(0, 8) }}
            </div>
          </div>
          <div class="ws-item-actions">
            <AppButton
              v-if="!ws.active"
              size="sm"
              variant="ghost"
              :busy="busy"
              @click="activateWorkspace(ws)"
            >
              切换到这里
            </AppButton>
            <AppButton
              v-if="!ws.active"
              size="sm"
              variant="danger-ghost"
              :busy="busy && deletingId === ws.id"
              @click="deleteWorkspace(ws)"
            >
              <AppIcon :icon="IconTrash" :size="14" /> 删除
            </AppButton>
            <span v-if="ws.active" class="ws-current-hint">
              <AppIcon :icon="IconCheck" :size="14" /> 当前活跃
            </span>
          </div>
        </li>
      </ul>

      <p class="ws-tip">
        <AppIcon :icon="IconAlertTriangle" :size="14" />
        切换工作空间会立即加载对应的数据库与文件。删除操作不可恢复。
      </p>
    </section>

    <p v-if="error" class="form-error">{{ error }}</p>

    <template #footer>
      <AppButton type="button" @click="emit('close')">关闭</AppButton>
      <AppButton variant="primary" :disabled="busy || !name.trim()" @click="saveSettings">
        保存项目名称
      </AppButton>
    </template>
  </AppModal>
</template>

<style scoped lang="scss">
.settings-section {
  margin-bottom: 24px;
  &:last-of-type { margin-bottom: 0; }
}

.settings-h {
  font-size: 13px;
  font-weight: 600;
  color: var(--text-primary, #1F2937);
  display: flex;
  align-items: center;
  gap: 6px;
  margin: 0 0 12px 0;
}

.settings-note {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 12px;
  color: var(--text-tertiary, #6B7280);
  padding: 10px 12px;
  background: var(--canvas, #F5F7FA);
  border-radius: 8px;
  margin-top: 8px;
}

.ws-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 12px;
}

.ws-add {
  padding: 16px;
  background: var(--canvas, #F5F7FA);
  border-radius: 12px;
  margin-bottom: 16px;
  border: 1px dashed var(--stroke-subtle, #E5E7EB);
}

.ws-loading {
  display: flex;
  align-items: center;
  gap: 8px;
  color: var(--text-tertiary);
  padding: 16px;
}

.ws-empty {
  color: var(--text-tertiary);
  text-align: center;
  padding: 24px;
  font-size: 13px;
}

.ws-list {
  list-style: none;
  padding: 0;
  margin: 0;
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.ws-item {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 14px 16px;
  background: #fff;
  border: 1px solid var(--stroke-subtle, #E5E7EB);
  border-radius: 10px;
  transition: all 0.15s;

  &--active {
    background: var(--accent-tint, #EEF2FF);
    border-color: var(--primary, #4F6EF7);
    border-width: 1.5px;
  }
}

.ws-item-main {
  flex: 1;
  min-width: 0;
}

.ws-item-name {
  font-weight: 600;
  font-size: 14px;
  color: var(--text-primary, #1F2937);
  display: flex;
  align-items: center;
  gap: 8px;
}

.ws-item-meta {
  font-size: 12px;
  color: var(--text-tertiary, #6B7280);
  margin-top: 4px;
}

.ws-item-actions {
  display: flex;
  gap: 8px;
  align-items: center;
  flex-shrink: 0;
  margin-left: 16px;
}

.ws-current-hint {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  font-size: 12px;
  font-weight: 500;
  color: var(--primary, #4F6EF7);
}

.ws-badge {
  display: inline-flex;
  align-items: center;
  gap: 3px;
  padding: 2px 8px;
  border-radius: 100px;
  font-size: 11px;
  font-weight: 500;

  &--active {
    background: var(--primary, #4F6EF7);
    color: #fff;
  }
  &--legacy {
    background: #FEF3C7;
    color: #92400E;
  }
}

.ws-tip {
  display: flex;
  align-items: flex-start;
  gap: 6px;
  font-size: 11px;
  color: var(--text-tertiary);
  margin: 12px 0 0 0;
  line-height: 1.5;
}

.spin {
  animation: spin 1s linear infinite;
}
@keyframes spin {
  from { transform: rotate(0deg); }
  to   { transform: rotate(360deg); }
}

.form-error {
  color: var(--danger, #EF4444);
  font-size: 12px;
  margin: 4px 0 0 0;
}
</style>
