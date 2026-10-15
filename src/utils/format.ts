// 全局文案映射与格式化工具（沿用 React 版对应常量）。

export const roleNames: Record<string, string> = {
  admin: '管理员',
  internal: '所内研究人员',
  external: '所外研究人员',
};

export const statuses: Record<string, string> = {
  pending: '待处理',
  ready: '已规范化',
  review: '待复核',
  needs_ocr: '待 OCR',
  error: '处理失败',
  completed: '已完成',
  processing: '处理中',
  approved: '已确认',
  rejected: '不采用',
  published: '已发布',
  exported: '已导出',
};

export const purposes: Record<string, string> = {
  both: '训练 + 知识库',
  training: '训练数据',
  kb: '知识库',
};

export const licenses: Record<string, string> = {
  self_owned: '自有 / 已获授权',
  research_only: '仅内部知识库',
  unknown: '用途待核对',
};

export const trainingTypes: Record<string, string> = {
  pretrain: '正文预训练',
  sft: 'SFT',
  preference: '偏好',
  image: '图文',
};

export const date = (value?: string | null): string =>
  value
    ? new Date(value).toLocaleString('zh-CN', {
        month: '2-digit',
        day: '2-digit',
        hour: '2-digit',
        minute: '2-digit',
        hour12: false,
      })
    : '—';

export const size = (n: number): string =>
  n > 1048576
    ? (n / 1048576).toFixed(1) + ' MB'
    : Math.max(1, Math.round((n || 0) / 1024)) + ' KB';

export const loc = (value: unknown): string =>
  typeof value === 'string' ? value : JSON.stringify(value || '原文记录');

export const assetLabel = (a: any): string =>
  a?.provenance?.source_document_title
    ? `${a.provenance.source_document_title} · 第 ${(a.provenance.source_pages || []).join('、')} 页${a.provenance.artifact_type === 'sft' ? ' SFT' : '语料'}`
    : a?.name || '来源资料';

export const cleanTitle = (value: unknown): string =>
  String(value || '未命名文献').replace(/（(?:真实论文|章节管线合成示例)）\s*$/, '');
