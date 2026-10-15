<script setup lang="ts">
// 操作记录（对照 GlobalSearch.jsx AuditTrail）
import { computed, onMounted, ref } from 'vue';
import { IconSearch } from '@tabler/icons-vue';
import AppIcon from '../components/common/AppIcon.vue';
import { platformRequest } from '../api/client';

const rows = ref<any[]>([]);
const error = ref('');
const filter = ref('');

const eventNames: Record<string, string> = {
  batch_create: '建立文献批次',
  batch_run: '提交模型任务',
  batch_sample: '生成随机样本',
  batch_review: '记录抽检结论',
  batch_accept: '验收批次',
  issue_resolve: '关闭复核问题',
  package_created: '生成数据包',
  package_publish: '设置发布权限',
  data_link: '关联数据资产',
  data_ingest: 'Markdown 转入文献',
  ocr_requested: '提交 OCR 任务',
  ocr_completed: 'OCR 完成',
  ocr_config: '配置 OCR',
  prompt_save: '保存提示词版本',
  password_change: '修改本人密码',
  prompt_create: '创建提示词',
  prompt_edit: '修订提示词',
  prompt_default: '设置默认提示词',
  standard_draft: '整理规则草案',
  standard_generated: '模型归纳规则',
  standard_publish: '发布处理标准',
  standard_edit: '修改标准草案',
  login: '登录',
  logout: '退出登录',
  user_create: '创建账号',
  user_update: '修改账号',
  setup: '初始化管理员',
  system_migration: '历史数据归档',
};

const visible = computed(() =>
  rows.value.filter((r) => JSON.stringify(r).toLowerCase().includes(filter.value.toLowerCase())),
);

function eventLabel(r: any): string {
  return eventNames[r.event] || r.action || r.event;
}

function detailText(r: any): string {
  if (typeof r.detail === 'string') return r.detail;
  return JSON.stringify(r.detail || r.details || r.payload || {});
}

onMounted(() => {
  void platformRequest('/api/platform/audit')
    .then((data) => {
      rows.value = data.audit || data.events || data.logs || [];
    })
    .catch((e) => {
      error.value = e instanceof Error ? e.message : String(e);
    });
});
</script>

<template>
  <div class="ph-page">
    <div class="ph-section-banner">
      <div class="page-heading">
        <div>
          <h1>操作记录</h1>
          <p>关联操作人、文献批次、复核结论与产出动作。</p>
        </div>
        <label class="ph-banner-search">
          <AppIcon :icon="IconSearch" :size="17" />
          <input v-model="filter" aria-label="筛选操作记录" placeholder="筛选姓名、批次或操作" />
        </label>
      </div>
    </div>
    <div v-if="error" class="ph-error">{{ error }}</div>

    <section class="card ph-pad">
      <div class="table-wrap">
        <table>
          <thead>
            <tr><th>时间</th><th>操作人</th><th>操作</th><th>批次 / 内容</th></tr>
          </thead>
          <tbody>
            <tr v-for="r in visible" :key="r.id || r.created_at">
              <td class="tiny">{{ new Date(r.created_at).toLocaleString('zh-CN') }}</td>
              <td>{{ r.actor_name || r.display_name || r.actor?.display_name || r.username || r.actor_id }}</td>
              <td>{{ eventLabel(r) }}</td>
              <td class="ph-audit-detail">
                <a v-if="r.batch_id" :href="'#batches?batch=' + r.batch_id">{{ r.batch_name || r.batch_id }}</a>
                <div>{{ detailText(r) }}</div>
              </td>
            </tr>
          </tbody>
        </table>
      </div>
      <div v-if="!visible.length" class="ph-empty">暂无匹配的操作记录</div>
    </section>
  </div>
</template>
