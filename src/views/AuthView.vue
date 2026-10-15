<script setup lang="ts">
import { reactive } from 'vue';
import { IconLeaf, IconLock, IconArrowRight } from '@tabler/icons-vue';

defineProps<{
  setup: boolean;
  localSetupAllowed: boolean;
  error: string;
  busy: boolean;
}>();

const emit = defineEmits<{
  (e: 'submit', form: { username: string; password: string; display_name: string }): void;
  (e: 'reload'): void;
}>();

const form = reactive({ username: '', password: '', display_name: '' });

function submit(): void {
  emit('submit', { ...form });
}
</script>

<template>
  <div class="auth-shell">
    <section class="auth-intro">
      <div class="auth-brand">
        <IconLeaf size="34" />
        植物科研数据工坊
      </div>
      <h1>让科研资料<br />成为可复用的数据。</h1>
      <p>从原文献到章节语料，从批次抽检到训练数据与知识库，保留每一步的来源和判断。</p>
      <ol>
        <li>归集文献与 OCR 成果</li>
        <li>按批加工、对照原文抽检</li>
        <li>发布可追溯的数据包</li>
      </ol>
      <span class="auth-local">科研工作空间 · 数据保存在运行服务的电脑</span>
    </section>
    <section class="auth-card">
      <IconLock class="auth-lock" :size="28" />
      <h2>{{ setup ? (localSetupAllowed ? '创建工作空间管理员' : '等待管理员初始化') : '登录工作空间' }}</h2>
      <p>
        {{
          setup
            ? localSetupAllowed
              ? '首次启用，请设置管理员账号。之后可在账号管理中添加研究人员。'
              : '请管理员在运行服务的电脑上，通过本机地址创建首个账号。完成后，局域网成员即可使用分配的账号登录。'
            : '使用管理员分配的本地账号登录。'
        }}
      </p>
      <form v-if="!setup || localSetupAllowed" @submit.prevent="submit">
        <label>
          账号
          <input v-model="form.username" required autoComplete="username" maxlength="64" />
        </label>
        <label v-if="setup">
          姓名
          <input v-model="form.display_name" required autoComplete="name" maxlength="80" />
        </label>
        <label>
          密码
          <input
            v-model="form.password"
            required
            type="password"
            :minlength="setup ? 10 : 1"
            :autoComplete="setup ? 'new-password' : 'current-password'"
          />
        </label>
        <small v-if="setup">至少 10 位。此密码只用于当前数据工坊。</small>
        <div v-if="error" class="ph-error" role="alert">{{ error }}</div>
        <button class="button primary" :disabled="busy">
          {{ busy ? '正在验证…' : setup ? '创建并进入' : '登录' }}
          <IconArrowRight :size="18" />
        </button>
      </form>
      <button class="text-button" type="button" @click="emit('reload')">重新连接</button>
    </section>
  </div>
</template>
