// 与后端交互的领域类型。响应体大多来自 Python 后端，业务字段保持宽松，
// 只在需要精确访问处给出明确形状。

export type Role = 'admin' | 'internal' | 'external';

export interface User {
  id: string;
  username: string;
  display_name: string;
  role: Role;
  active?: boolean | number;
}

export interface WorkspaceSettings {
  project_name: string;
}

export interface WorkspaceListItem {
  id: string;
  name: string;
  created_at: string;
  active: boolean;
  exists: boolean;
  legacy?: boolean;
}

export interface WorkspaceState {
  stats: { assets: number; normalized: number; pending: number; versions: number };
  assets: unknown[];
  jobs: unknown[];
  reviews: unknown[];
  versions: unknown[];
  logs: unknown[];
  settings: WorkspaceSettings;
  chapter_summary?: { documents?: number; chapters?: number; review?: number };
  workspaces?: WorkspaceListItem[];
  active_workspace?: string;
}

export interface PromptItem {
  id: string;
  name?: string;
  title?: string;
  type: 'generic' | 'special';
  version: number;
  status: string;
  is_default?: boolean;
  content?: string;
  created_at: string;
  batch_ids?: string[];
  issue_ids?: string[];
  origin?: string;
}

export interface StandardItem extends PromptItem {
  origin: 'manual' | 'model';
  batch_ids: string[];
  issue_ids: string[];
}

export interface BatchItem {
  id: string;
  name: string;
  status: string;
  document_count?: number;
  document_ids?: string[];
  chapter_count?: number;
  sampling_rate?: number;
  accepted_at?: string | null;
  created_at: string;
  type?: string;
}

export interface PackageItem {
  id: string;
  name: string;
  type: string;
  status?: string;
  published?: boolean;
  minimum_role: Role;
  created_at: string;
  metadata?: Record<string, any>;
  source_documents?: Array<Record<string, any>>;
  files?: unknown[];
  download_url?: string;
  previews?: unknown[];
}

export interface DocumentItem {
  id: string;
  title: string;
  document_type?: string;
  page_count?: number;
  chapter_count?: number;
  source_metadata?: Record<string, any>;
}
