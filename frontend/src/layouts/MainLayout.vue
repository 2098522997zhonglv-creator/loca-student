<template>
  <a-layout class="shell">
    <a-layout-header class="header">
      <div class="brand-block" @click="router.push('/dashboard')">
        <img class="brand-mark" :src="brandLogoUrl" alt="" />
        <div class="brand-copy">
          <div class="brand">本地知识中心</div>
          <div class="brand-sub">Knowledge Studio</div>
        </div>
      </div>

      <div class="project-wrap">
        <span class="project-label">当前项目</span>
        <a-select
          v-model="selectedProject"
          placeholder="选择项目"
          allow-search
          :style="{ width: '240px' }"
          @change="changeProject"
        >
          <a-option
            v-for="item in projectStore.projectOptions"
            :key="item.value"
            :value="item.value"
          >
            {{ item.label }}
          </a-option>
        </a-select>
      </div>

      <div class="spacer" />

      <div class="user-chip">
        <span class="user-dot" />
        <span class="user-name">{{ auth.user?.username }}</span>
      </div>
      <a-button class="logout-btn" type="outline" @click="logout">退出</a-button>
    </a-layout-header>

    <a-layout class="body">
      <a-layout-sider :width="220" class="sider">
        <nav class="nav">
          <section v-for="group in menuGroups" :key="group.title" class="nav-group">
            <div class="nav-title">{{ group.title }}</div>
            <button
              v-for="item in group.items"
              :key="item.path"
              type="button"
              class="nav-item"
              :class="{ active: active === item.path }"
              @click="navigate(item.path)"
            >
              <span class="nav-dot" />
              {{ item.label }}
            </button>
          </section>
        </nav>
      </a-layout-sider>
      <a-layout-content class="content">
        <div class="content-frame">
          <router-view />
        </div>
      </a-layout-content>
    </a-layout>
  </a-layout>
</template>

<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useAuthStore } from '@/store/authStore'
import { useProjectStore } from '@/store/projectStore'
import { brandLogoUrl } from '@/utils/assetUrl'

const router = useRouter()
const route = useRoute()
const auth = useAuthStore()
const projectStore = useProjectStore()
const selectedProject = ref<number | undefined>(projectStore.currentProject?.id)

const menuGroups = [
  {
    title: '工作台',
    items: [
      { path: '/dashboard', label: '概览' },
      { path: '/projects', label: '项目管理' },
      { path: '/knowledge-management', label: '知识库' },
      { path: '/requirements', label: '需求评审' },
      { path: '/langgraph-chat', label: '知识问答' },
      { path: '/file-management', label: '文件管理' },
    ],
  },
  {
    title: '配置',
    items: [
      { path: '/llm-configs', label: 'LLM 配置' },
      { path: '/remote-mcp-configs', label: 'MCP 配置' },
      { path: '/skills', label: '技能管理' },
      { path: '/api-keys', label: 'API 密钥' },
    ],
  },
  {
    title: '系统',
    items: [
      { path: '/users', label: '用户管理' },
      { path: '/organizations', label: '组织管理' },
      { path: '/permissions', label: '权限管理' },
      { path: '/operation-logs', label: '操作日志' },
      { path: '/system-runtime-logs', label: '运行日志' },
    ],
  },
]

const active = computed(() => '/' + route.path.split('/')[1])

onMounted(async () => {
  await projectStore.fetchProjects()
  selectedProject.value = projectStore.currentProject?.id
})

function navigate(path: string) {
  router.push(path)
}

function changeProject(value: string | number) {
  const project = projectStore.projectList.find((item) => item.id === Number(value))
  if (project) projectStore.setCurrentProject(project)
}

async function logout() {
  auth.logout()
  await router.push('/login')
}
</script>

<style scoped>
.shell {
  min-height: 100vh;
  background:
    radial-gradient(1200px 480px at 12% -10%, rgba(126, 224, 200, 0.28), transparent 55%),
    radial-gradient(900px 420px at 90% 0%, rgba(15, 118, 110, 0.12), transparent 50%),
    linear-gradient(180deg, #eef4f2 0%, #e4eeea 100%);
}

.header {
  height: 68px;
  display: flex;
  align-items: center;
  gap: 20px;
  padding: 0 22px;
  background: rgba(255, 255, 255, 0.78);
  border-bottom: 1px solid rgba(15, 61, 56, 0.08);
  backdrop-filter: blur(12px);
}

.brand-block {
  display: flex;
  align-items: center;
  gap: 12px;
  cursor: pointer;
  min-width: 196px;
}

.brand-mark {
  width: 36px;
  height: 36px;
  border-radius: 10px;
  box-shadow: 0 8px 18px rgba(15, 61, 56, 0.18);
}

.brand-copy {
  display: flex;
  flex-direction: column;
  line-height: 1.15;
}

.brand {
  font-family: var(--kc-display);
  font-size: 18px;
  font-weight: 700;
  color: var(--kc-forest);
  letter-spacing: 0.01em;
}

.brand-sub {
  font-size: 11px;
  color: var(--theme-text-tertiary);
  letter-spacing: 0.08em;
  text-transform: uppercase;
}

.project-wrap {
  display: flex;
  align-items: center;
  gap: 10px;
}

.project-label {
  font-size: 12px;
  color: var(--theme-text-tertiary);
}

.spacer {
  flex: 1;
}

.user-chip {
  display: inline-flex;
  align-items: center;
  gap: 8px;
  padding: 6px 12px;
  border-radius: 999px;
  background: rgba(15, 61, 56, 0.06);
  color: var(--theme-text-secondary);
  font-size: 13px;
}

.user-dot {
  width: 8px;
  height: 8px;
  border-radius: 50%;
  background: #0f766e;
  box-shadow: 0 0 0 4px rgba(15, 118, 110, 0.15);
}

.logout-btn {
  border-color: rgba(15, 61, 56, 0.18) !important;
  color: var(--kc-forest) !important;
}

.body {
  min-height: calc(100vh - 68px);
}

.sider {
  background: rgba(255, 255, 255, 0.72);
  border-right: 1px solid rgba(15, 61, 56, 0.08);
  backdrop-filter: blur(10px);
}

.nav {
  padding: 18px 12px 28px;
  display: flex;
  flex-direction: column;
  gap: 18px;
}

.nav-group {
  display: flex;
  flex-direction: column;
  gap: 4px;
}

.nav-title {
  padding: 0 12px 8px;
  font-size: 11px;
  letter-spacing: 0.12em;
  text-transform: uppercase;
  color: #7d948c;
}

.nav-item {
  appearance: none;
  border: 0;
  background: transparent;
  text-align: left;
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 10px 12px;
  border-radius: 12px;
  color: #35524a;
  font-size: 14px;
  font-family: inherit;
  cursor: pointer;
  transition: background 0.18s ease, color 0.18s ease, transform 0.18s ease;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.nav-item:hover {
  background: rgba(15, 118, 110, 0.08);
  color: var(--kc-forest);
}

.nav-item.active {
  background: linear-gradient(135deg, rgba(15, 61, 56, 0.95), rgba(15, 118, 110, 0.92));
  color: #edfaf6;
  box-shadow: 0 10px 22px rgba(15, 61, 56, 0.2);
}

.nav-dot {
  width: 6px;
  height: 6px;
  border-radius: 50%;
  background: currentColor;
  opacity: 0.55;
}

.content {
  padding: 22px;
  min-width: 0;
}

.content-frame {
  min-height: calc(100vh - 112px);
  animation: rise-in 0.45s ease both;
}

@keyframes rise-in {
  from {
    opacity: 0;
    transform: translateY(8px);
  }
  to {
    opacity: 1;
    transform: translateY(0);
  }
}

@media (max-width: 960px) {
  .brand-sub,
  .project-label {
    display: none;
  }
  .content {
    padding: 14px;
  }
}
</style>
