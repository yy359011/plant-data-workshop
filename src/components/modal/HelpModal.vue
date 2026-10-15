<script setup lang="ts">
import AppModal from '../common/AppModal.vue';

const emit = defineEmits<{ (e: 'close'): void }>();

const items: Array<[string, string, string]> = [
  ['01', '归集与关联', '在数据资产导入原始文件，或从文献目录导入每篇文献的逐页 Markdown / JSON 与 images 文件夹。原始 PDF 可补传；未识别资料可以提交 OCR。'],
  ['02', '处理与抽检', '在文献管理的"处理与抽检"建立批次，跟踪 AI 任务，随机抽取样本。在同一文献详情页对照 PDF，修订正文和字段并记录结论。'],
  ['03', '验收与发布', '完成样本核查、关闭问题后验收批次，再生成训练或知识库数据包。产出中心展示文件、来源与统计，管理员设置发布和下载权限。'],
];
</script>

<template>
  <AppModal title="使用数据工坊" @close="emit('close')">
    <div class="help-content">
      <div v-for="[n, t, p] in items" :key="n">
        <span>{{ n }}</span>
        <section>
          <h3>{{ t }}</h3>
          <p>{{ p }}</p>
        </section>
      </div>
      <div class="small-notice">
        OCR 与章节模型需配置实际服务。平台生产训练数据；知识库采用本地关键词检索，结果保留来源。抽检通过率只描述样本。
      </div>
    </div>
  </AppModal>
</template>
