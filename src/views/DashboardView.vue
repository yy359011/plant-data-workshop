<script setup lang="ts">
import { computed, onMounted, ref } from 'vue';
import {
  IconSearch,
  IconHistory,
  IconDatabase,
  IconShieldCheck,
  IconFileDescription,
  IconArrowRight,
  IconBox,
  IconBook2,
  IconPackage,
  IconCheck,
} from '@tabler/icons-vue';
import AppHeading from '../components/common/AppHeading.vue';
import AppButton from '../components/common/AppButton.vue';
import AppIcon from '../components/common/AppIcon.vue';
import bannerImage from '../images/banner.png';
import { workspace, go } from '../stores/workspace';
import { platformRequest } from '../api/client';
import { mockPackages } from '../data/mockPackages';

const data = computed(() => workspace.data);
const summary = computed(() => workspace.data.chapter_summary || {});

// 产出包数量（与产出中心卡片列表口径一致：合成示例包 + 后端真实包）
const packageCount = ref(mockPackages.length);
onMounted(async () => {
  try {
    const value = await platformRequest('/api/packages');
    packageCount.value = mockPackages.length + (value.packages || []).length;
  } catch {
    packageCount.value = mockPackages.length;
  }
});
</script>

<template>
  <div class="ph-hero-banner">
    <img class="ph-hero-banner-img" :src="bannerImage" alt="" aria-hidden="true" />
    <div class="ph-hero-banner-overlay"></div>
    <AppHeading title="科研数据工作台" subtitle="归集资料，按批次加工与抽检，发布可复用的数据包。" />

    <div class="ph-hub-stats">
      <button
        v-for="item in [
          { label: '原始资料', value: data.stats.assets, to: 'assets', icon: IconBox },
          { label: '文献', value: summary.documents || 0, to: 'library', icon: IconBook2 },
          { label: '产出包', value: packageCount, to: 'outputs', icon: IconPackage },
          { label: '待确认章节', value: summary.review || 0, to: 'batches', icon: IconCheck },
        ]"
        :key="item.label"
        class="ph-hub-stat"
        @click="go(item.to)"
      >
        <div class="ph-hub-stat-icon">
          <AppIcon :icon="item.icon" :size="22" />
        </div>
        <div class="ph-hub-stat-body">
          <span>{{ item.label }}</span>
          <strong>{{ item.value }}</strong>
        </div>
      </button>
    </div>
  </div>

  <section class="card ph-pad">
    <h2>从资料到数据包</h2>
    <div class="production-journey">
      <button
        v-for="item in [
          ['01', '归集数据资产', 'PDF、元数据、逐页 Markdown、图像与已加工语料统一归档。', 'assets', IconDatabase],
          ['02', '处理与抽检', '从文献管理建立批次，选择提示词，追踪 AI 任务并随机抽检。', 'batches', IconShieldCheck],
          ['03', '发布与使用', '从已验收批次生成训练或知识库数据包，设置权限并检索成果。', 'outputs', IconFileDescription],
        ]"
        :key="item[0]"
        @click="go(item[3] as string)"
      >
        <span class="journey-number">{{ item[0] }}</span>
        <AppIcon :icon="item[4]" :size="27" />
        <h3>{{ item[1] }}</h3>
        <p>{{ item[2] }}</p>
        <span class="text-button"
          >进入<AppIcon :icon="IconArrowRight" :size="16" /></span>
      </button>
    </div>
  </section>

  <div class="dual-grid">
    <section class="card ph-pad ph-market-card" @click="go('knowledge')">
      <div class="ph-market-card-head">
        <div class="ph-market-card-icon ph-market-card-icon--violet">
          <AppIcon :icon="IconSearch" :size="22" />
        </div>
        <h2>检索已有成果</h2>
        <span class="ph-market-badge">知识库</span>
      </div>
      <p class="muted">按物种、性状或主题查找知识库分段内容，每条结果保留文献与原页来源。</p>
      <div class="ph-market-card-action">
        <AppButton :icon="IconSearch" @click.stop="go('knowledge')">知识库检索</AppButton>
      </div>
    </section>
    <section class="card ph-pad ph-market-card" @click="go('logs')">
      <div class="ph-market-card-head">
        <div class="ph-market-card-icon ph-market-card-icon--teal">
          <AppIcon :icon="IconHistory" :size="22" />
        </div>
        <h2>操作有据可查</h2>
        <span class="ph-market-badge">审计</span>
      </div>
      <p class="muted">复核、修订、批次验收和发布均记录操作人。管理员可以将重复问题整理成处理标准。</p>
      <div class="ph-market-card-action">
        <AppButton :icon="IconHistory" @click.stop="go('logs')">查看操作记录</AppButton>
      </div>
    </section>
  </div>
</template>
