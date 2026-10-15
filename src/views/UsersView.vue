<script setup lang="ts">
// 账号与角色（对照 PlatformAuth.jsx AccountManagement）
import { onMounted, ref } from 'vue';
import { IconUsers } from '@tabler/icons-vue';
import AppIcon from '../components/common/AppIcon.vue';
import { roleNames } from '../utils/format';
import { platformRequest } from '../api/client';
import { setToast } from '../stores/workspace';

const props = defineProps<{ user: any }>();

const users = ref<any[]>([]);
const error = ref('');
const busy = ref(false);
const form = ref({ username: '', display_name: '', password: '', role: 'internal' });
const reset = ref<any>(null);
const password = ref('');

async function load(): Promise<void> {
  try {
    users.value = (await platformRequest('/api/platform/users')).users || [];
  } catch (e) {
    error.value = e instanceof Error ? e.message : String(e);
  }
}

async function action(fn: () => Promise<unknown>): Promise<void> {
  busy.value = true;
  error.value = '';
  try {
    await fn();
    await load();
    setToast('账号设置已保存');
  } catch (e) {
    error.value = e instanceof Error ? e.message : String(e);
  } finally {
    busy.value = false;
  }
}

function createUser(): void {
  void action(async () => {
    await platformRequest('/api/platform/users', form.value);
    form.value = { username: '', display_name: '', password: '', role: 'internal' };
  });
}

function savePassword(): void {
  void action(async () => {
    await platformRequest('/api/platform/users/' + reset.value.id, { password: password.value });
    reset.value = null;
    password.value = '';
  });
}

onMounted(load);
</script>

<template>
  <div class="ph-page">
    <div class="ph-section-banner">
      <div class="page-heading">
        <div>
          <h1>账号与角色</h1>
          <p>管理员管理工作空间；研究人员按授权使用资料和数据包。</p>
        </div>
      </div>
    </div>
    <div v-if="error" class="ph-error">{{ error }}</div>

    <section class="card ph-pad">
      <h2>新建账号</h2>
      <form class="ph-form-grid" @submit.prevent="createUser">
        <label>
          姓名
          <input v-model="form.display_name" required />
        </label>
        <label>
          账号
          <input v-model="form.username" required />
        </label>
        <label>
          初始密码
          <input v-model="form.password" type="password" required minlength="10" autoComplete="new-password" />
        </label>
        <label>
          角色
          <select v-model="form.role">
            <option v-for="(v, k) in roleNames" :key="k" :value="k">{{ v }}</option>
          </select>
        </label>
        <button class="button primary" :disabled="busy">创建账号</button>
      </form>
    </section>

    <section class="card ph-pad">
      <h2>工作空间成员</h2>
      <div class="table-wrap">
        <table>
          <thead>
            <tr><th>姓名 / 账号</th><th>角色</th><th>状态</th><th>管理</th></tr>
          </thead>
          <tbody>
            <tr v-for="u in users" :key="u.id">
              <td>
                <strong>{{ u.display_name }}</strong>
                <small class="block muted">{{ u.username }}</small>
              </td>
              <td>
                <select
                  :aria-label="`修改 ${u.display_name} 的角色`"
                  :value="u.role"
                  :disabled="busy || u.id === user.id"
                  @change="action(() => platformRequest('/api/platform/users/' + u.id, { role: ($event.target as HTMLSelectElement).value }))"
                >
                  <option v-for="(v, k) in roleNames" :key="k" :value="k">{{ v }}</option>
                </select>
              </td>
              <td>{{ u.active === false || u.active === 0 ? '已停用' : '启用中' }}</td>
              <td>
                <div class="inline">
                  <button class="text-button" @click="reset = u; password = ''">重置密码</button>
                  <button
                    class="text-button"
                    :disabled="busy || u.id === user.id"
                    @click="action(() => platformRequest('/api/platform/users/' + u.id, { active: !(u.active === false || u.active === 0) }))"
                  >
                    {{ u.active === false || u.active === 0 ? '启用' : '停用' }}
                  </button>
                </div>
              </td>
            </tr>
          </tbody>
        </table>
      </div>
      <form v-if="reset" class="ph-inline-form" @submit.prevent="savePassword">
        <label>
          {{ reset.display_name }} 的新密码
          <input v-model="password" type="password" required minlength="10" autoComplete="new-password" />
        </label>
        <button class="button primary" :disabled="busy">保存新密码</button>
        <button type="button" class="button" @click="reset = null">取消</button>
      </form>
    </section>

    <div class="notice">
      <AppIcon :icon="IconUsers" />
      <span>所外研究人员仅能查看和下载为其开放的数据包；所内研究人员可加工与抽检；提示词、服务配置、账号和发布权限由管理员管理。</span>
    </div>
  </div>
</template>
