// 工作台全局状态（reactive 单例，替代 Pinia/React state）。
import { reactive } from 'vue';
import { ElMessage } from 'element-plus';
import { api } from '../api/client';
import type { User, WorkspaceState } from '../types/api';

const initialState: WorkspaceState = {
  stats: { assets: 0, normalized: 0, pending: 0, versions: 0 },
  assets: [],
  jobs: [],
  reviews: [],
  versions: [],
  logs: [],
  settings: { project_name: '植物科学研究集' },
};

export interface WorkspaceStore {
  user: User | null;
  page: string;
  routeHash: string;
  data: WorkspaceState;
  loading: boolean;
  error: string;
  modal: string | null;
  busy: boolean;
  search: string;
  asset: any;
  leaveTarget: string | null;
  chapterImport: any;
  chapterDirty: boolean;
  acceptedHash: string;
}

export const workspace = reactive<WorkspaceStore>({
  user: null,
  page: 'dashboard',
  routeHash: '#dashboard',
  data: { ...initialState },
  loading: true,
  error: '',
  modal: null,
  busy: false,
  search: '',
  asset: null,
  leaveTarget: null,
  chapterImport: null,
  chapterDirty: false,
  acceptedHash: '#dashboard',
});

export function routeOf(hash: string): string {
  return hash.slice(1).split('?')[0];
}

/** 章节工作台存在未保存修改时，拦截离开导航 */
export function isLeavingDocument(target: string): boolean {
  return workspace.page === 'chapters' && workspace.chapterDirty && target !== workspace.acceptedHash;
}

/** 刷新工作台聚合数据（外部角色无权访问平台状态） */
export async function refreshWorkspace(): Promise<void> {
  if (workspace.user?.role === 'external') {
    workspace.loading = false;
    return;
  }
  try {
    workspace.data = { ...initialState, ...(await api('/state')) };
    workspace.error = '';
  } catch (error) {
    workspace.error = error instanceof Error ? error.message : String(error);
  } finally {
    workspace.loading = false;
  }
}

/** 统一导航入口：维护 hash 深链接、守卫未保存章节 */
export function go(target: string): void {
  const hash = target.startsWith('#') ? target : '#' + target;
  const route = hash.slice(1).split('?')[0];
  if (isLeavingDocument(hash)) {
    workspace.leaveTarget = hash;
    return;
  }
  location.hash = hash.slice(1);
  workspace.routeHash = hash;
  workspace.page = route;
  workspace.acceptedHash = hash;
  window.scrollTo(0, 0);
}

/** 包装平台操作：锁定 busy、成功后刷新工作台、失败提示 */
export async function act(fn: () => Promise<unknown>, message?: string): Promise<unknown> {
  workspace.busy = true;
  try {
    const value = await fn();
    await refreshWorkspace();
    if (message) setToast(message);
    return value;
  } catch (error) {
    setToast(error instanceof Error ? error.message : String(error));
    throw error;
  } finally {
    workspace.busy = false;
  }
}

export async function viewAsset(id: string): Promise<void> {
  try {
    workspace.asset = await api('/assets/' + id);
    workspace.modal = 'asset';
  } catch (error) {
    setToast(error instanceof Error ? error.message : String(error));
  }
}

export function setToast(message: string): void {
  ElMessage({ message, type: 'info', grouping: true, duration: 4000 });
}

/** 角色菜单过滤：与 App.jsx 保持一致 */
export function allowedMenu(role?: string): Array<[string, string]> {
  const all: Array<[string, string]> = [
    ['dashboard', '工作台'],
    ['assets', '数据资产'],
    ['library', '文献管理'],
    ['batches', '处理与抽检'],
    ['outputs', '产出中心'],
    ['knowledge', '知识库检索'],
    ['logs', '操作记录'],
    ['prompts', '提示词管理'],
    ['users', '账号与角色'],
    ['services', '服务配置'],
  ];
  if (role === 'external') return all.filter(([k]) => ['outputs', 'knowledge'].includes(k));
  if (role === 'internal') return all.filter(([k]) => !['prompts', 'users', 'services'].includes(k));
  return all;
}
