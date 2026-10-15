# plant-data-workshop 前端 React → Vue 3 迁移计划

## Context

当前前端为 React 19 + JSX（`src/` 共 18 个文件，约 1,500 行密集 JSX + 约 470 行 CSS），后端为 Python（`backend/server.py`，API 走 `/api/*`，Cookie 会话）。用户要求将前端整体转为 **Vue 3 Composition API + `<script setup>` + TypeScript + Element Plus + SCSS**，PC 优先（1920 设计稿）并兼顾 1440/1200/1024/992/768 断点，禁用 Tailwind / CSS-in-JS。

已确认决策：
- UI 组件库：**Element Plus**（zh-cn locale）
- 语言：**TypeScript**
- 旧 React 源码：移动到 `legacy-react/` 保留备份（项目无 git，不可直接删除）
- Python 后端、API 契约、数据目录 **完全不动**

## 关键架构决策

1. **路由**：不用 vue-router。现有 hash 深链接格式为 `#chapters?document=xxx`（无 `/` 前缀），AGENTS.md 要求既有深链接保持有效。用轻量组合式路由 `src/composables/useHashRoute.ts` 1:1 复刻现有 `App.jsx:43-53` 的 hashchange/click 拦截与「章节未保存离开确认」语义（`chapter-unsaved` 事件 + `acceptedHash` 回退）。
2. **状态**：不引入 Pinia。用 `src/stores/workspace.ts`（`reactive` + 组合式函数）承载现 `shared={data,go,act,busy,setToast,viewAsset,user}` 上下文，行为与现有 `refresh()/act()` 模式一致。
3. **Element Plus 使用范围**：表单控件（el-input/el-select/el-form/el-checkbox）、弹窗（el-dialog 替代自研 Modal，保留焦点陷阱语义由 EP 提供）、消息（ElMessage 替代自研 toast）、标签（el-tag 替代 Badge）、表格（el-table 或保留原生 table+SCSS，按页面择优）、tabs、上传交互保留自研 dropzone（拖拽+校验逻辑简单直接）。**布局壳层（sidebar/topbar/card/page-heading）保留自研 SCSS**，保证 1:1 视觉。
4. **主题**：通过 CSS 变量把 EP 主色映射到现有品牌色：`--el-color-primary: #0d99bb`（现 `--blue`）、success/warning/danger 对应现有 badge 配色，字体继承现有 PingFang/Microsoft YaHei 栈。
5. **图标**：`@tabler/icons-vue`（与现 `@tabler/icons-react` 同一套 Tabler 图标，视觉零变化）。
6. **构建链不动**：`scripts/dev.py`、`scripts/prepare-sites-build.mjs`、`scripts/prepare-pdf-assets.mjs`、`worker/index.js`、`tests/`、`.openai/hosting.json` 全部保持原样；`vite build` 仍产出 `dist/client/index.html`，Sites 交接链兼容。

## 目标目录结构

```
src/
  main.ts                    # createApp + ElementPlus(zh-cn) + 全局 SCSS
  App.vue                    # AuthGate 判定：未登录→AuthView，已登录→AppShell
  env.d.ts                   # .vue 模块声明
  api/
    client.ts                # 现 App.jsx api()：fetch /api + 401 派发 session-expired
    platform.ts              # 现 PlatformAuth.jsx platformAPI()
  composables/
    useHashRoute.ts          # hash 路由 + 未保存守卫
    usePdfDocument.ts        # pdfjs 加载/渲染（自 PdfViewer.jsx 移植）
  stores/
    workspace.ts             # reactive 全局工作区状态 + act/refresh
  types/api.ts               # 后端数据结构 TS 类型
  components/
    AppShell.vue             # sidebar/topbar/页面分发/toast/全局 modal（App.jsx Workspace）
    PageHeading.vue  StatusBadge.vue  EmptyState.vue  PdfViewer.vue
    modal/ ImportModal.vue AssetModal.vue SettingsModal.vue HelpModal.vue AccountModal.vue
  views/
    AuthView.vue             # 登录/首次创建管理员（AuthGate）
    DashboardView.vue  DataHubView.vue  LibraryView.vue  ChapterWorkshopView.vue
    BatchWorkspaceView.vue  OutputCatalogView.vue  KnowledgeSearchView.vue
    GlobalSearchView.vue  AuditTrailView.vue  PromptAdminView.vue
    UsersView.vue  ServicesView.vue   # ServicesView 含 ServiceSettings + OCRSettings
  styles/
    _variables.scss _base.scss _element-plus.scss
    shell.scss dashboard.scss data-hub.scss library.scss chapter-workshop.scss
    batch.scss outputs.scss auth.scss
```

源文件 → 目标映射：`App.jsx`→AppShell+Dashboard；`PlatformAuth.jsx`→api/platform.ts+AuthView+UsersView+ServicesView(模型部分)+PasswordForm；`DataHub.jsx`→DataHubView；`DocumentLibrary.jsx`→LibraryView；`ChapterWorkshop.jsx`(402 行,最大)→ChapterWorkshopView+ChapterImportDialog；`BatchWorkspace.jsx`+`BatchReviewBar.jsx`→BatchWorkspaceView；`OutputCatalog.jsx`→OutputCatalogView+KnowledgeSearchView；`GlobalSearch.jsx`→GlobalSearchView+AuditTrailView；`PromptAdmin.jsx`→PromptAdminView；`PdfViewer.jsx`→PdfViewer.vue；4 个 CSS→styles/ SCSS。

## 实施步骤

1. **备份**：新建 `legacy-react/`，将 `src/` 整体移入（React 版只读存档），`public/`、`index.html` 留原地。
2. **工具链**：`package.json` 移除 `react/react-dom/@vitejs/plugin-react/@tabler/icons-react`，新增 `vue@^3.5`、`element-plus`、`@element-plus/icons-vue`、`@tabler/icons-vue`、`@vitejs/plugin-vue`、`typescript`、`sass-embedded`（devDep）；`vite.config.mjs` 换 `vue()` 插件、warmup 改 `./src/main.ts`，proxy/host 配置不动；`index.html` 改挂 `/src/main.ts`、`lang="zh-CN"`；新增 `tsconfig.json`、`src/env.d.ts`。
3. **地基**：styles SCSS（含 EP 主题变量映射 + 现有断点 1700/1300/960/768/480 迁入，并补 1440/1200/1024/992 微调）、api/ 两个客户端、types/api.ts、useHashRoute、workspace store。
4. **认证流**：AuthView（登录/首次 setup 双态）→ 手动验证登录 `admin`。
5. **壳层**：AppShell.vue（菜单权限过滤、全局搜索、面包屑、帮助/设置/账号弹窗、toast→ElMessage、未保存离开确认）+ DashboardView。
6. **功能页**（由简到繁）：UsersView → ServicesView → DataHubView → OutputCatalogView + KnowledgeSearchView → GlobalSearchView + AuditTrailView → PromptAdminView → LibraryView → BatchWorkspaceView。
7. **重点页**：ChapterWorkshopView + PdfViewer.vue（pdfjs-dist 6.4.299 逻辑照搬：worker 配置、canvas 渲染、页码/缩放、密码错误处理）+ ChapterImportDialog。
8. **收尾**：AGENTS.md 记录技术栈变更；`npm run build` + `npm run test:sites` + `npm run test:dev`。

## 验证

- 重启 dev 启动器（Python 后端 8766 + Vite 5173），浏览器走全链路：登录 `admin/admin123456` → 工作台统计/快捷入口 → 数据资产（导入弹窗、资料详情）→ 文献管理三级目录 → 章节工作台（PDF 渲染、页码切换、对照编辑、抽检栏）→ 批次 → 产出中心 → 知识库/全局检索 → 操作记录 → 提示词/账号/服务配置（管理员）。
- 深链接回归：直接打开 `#chapters?document=...` 能正确落页。
- 响应式：1920/1440/1200/1024/992/768 六档窗口宽度抽查侧栏折叠与栅格。
- `npm run build` 产物含 `dist/client/index.html`，`npm run test:sites`、`npm run test:dev` 全绿。
- 与 legacy-react 版本并排截图对比关键页面，布局/配色一致（EP 控件以主题映射贴近原视觉）。

## 风险

- `ChapterWorkshop.jsx` 单文件 402 行密集逻辑（文档/章节/字段提取/抽检/导入），是最易出错点 → 最后移植、逐功能对照。
- 自研 Modal 有焦点陷阱与 ESC 处理，换 el-dialog 后需确认关闭行为一致。
- `npm ci` 由 dev.py 依赖 stamp 触发，package.json 变更后首次重启会重装依赖，属预期。
