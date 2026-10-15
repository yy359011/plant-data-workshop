// 自写 hash 路由（保持 legacy 深链接格式：#chapters?document=xxx，无 / 前缀）。
// 所有 hash 导航必须经过 go()，以支持章节未保存守卫。

import { onMounted, onUnmounted } from 'vue';
import { workspace, isLeavingDocument } from '../stores/workspace';

/** 解析当前（或给定）hash 的查询参数 */
export function readHash(hash?: string): URLSearchParams {
  return new URLSearchParams((hash ?? location.hash).split('?')[1] || '');
}

/** 从 hash 提取路由段（不含查询参数） */
export function routeOf(hash: string): string {
  return hash.slice(1).split('?')[0];
}

/** 安装全局 hashchange / 锚点点击拦截：在离开含未保存章节的页面时弹出确认。 */
export function useHashRoute(): void {
  function onHashChange(): void {
    const target = location.hash || '#dashboard';
    if (isLeavingDocument(target)) {
      workspace.leaveTarget = target;
      window.history.pushState(null, '', workspace.acceptedHash);
      return;
    }
    workspace.acceptedHash = target;
    workspace.routeHash = target;
    workspace.page = routeOf(target);
    window.scrollTo(0, 0);
  }

  function onClick(event: MouseEvent): void {
    const anchor = (event.target as HTMLElement).closest?.('a[href^="#"]');
    if (!anchor || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey || event.button !== 0)
      return;
    const target = anchor.getAttribute('href');
    if (target && isLeavingDocument(target)) {
      event.preventDefault();
      event.stopPropagation();
      workspace.leaveTarget = target;
    }
  }

  onMounted(() => {
    // 手动应用初始 hash（浏览器加载页面时如果 hash 已存在，不会触发 hashchange）
    const initial = location.hash || '#dashboard';
    workspace.acceptedHash = initial;
    workspace.routeHash = initial;
    workspace.page = routeOf(initial);
    window.addEventListener('hashchange', onHashChange);
    document.addEventListener('click', onClick, true);
  });
  onUnmounted(() => {
    window.removeEventListener('hashchange', onHashChange);
    document.removeEventListener('click', onClick, true);
  });
}
