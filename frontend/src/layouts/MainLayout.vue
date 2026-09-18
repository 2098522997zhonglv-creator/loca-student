<template>
  <a-layout class="shell">
    <a-layout-header class="header">
      <div class="brand-block" role="link" tabindex="0" @click="router.push('/dashboard')" @keydown.enter="router.push('/dashboard')">
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
          class="project-select"
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

      <div v-if="isTestCaseManagementPage" class="view-switch">
        <a-radio-group v-model="testCaseActiveView" type="button" size="small">
          <a-radio value="list">列表视图</a-radio>
          <a-radio value="mindmap">思维导图</a-radio>
        </a-radio-group>
      </div>

      <div class="spacer" />

      <div class="header-actions">
        <div class="user-chip" :title="auth.user?.username || ''">
          <span class="user-dot" />
          <span class="user-name">{{ auth.user?.username }}</span>
        </div>
        <a-button class="logout-btn" type="text" @click="logout">退出</a-button>
      </div>
    </a-layout-header>

    <a-layout class="body">
      <a-layout-sider :width="212" class="sider">
        <nav class="nav" aria-label="主导航">
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
              <span class="nav-indicator" aria-hidden="true" />
              <span class="nav-label">{{ item.label }}</span>
            </button>
          </section>
        </nav>
      </a-layout-sider>
      <a-layout-content class="content">
        <div class="content-frame" :key="active">
          <router-view />
        </div>
      </a-layout-content>
    </a-layout>
  </a-layout>
</template>

<script setup lang="ts">
import { computed, onMounted, provide, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useAuthStore } from '@/store/authStore'
import { useProjectStore } from '@/store/projectStore'
import { brandLogoUrl } from '@/utils/assetUrl'

const router = useRouter()
const route = useRoute()
const auth = useAuthStore()
const projectStore = useProjectStore()
const selectedProject = ref<number | undefined>(projectStore.currentProject?.id)

const testCaseActiveView = ref<'list' | 'mindmap'>('list')
provide('testCaseActiveView', testCaseActiveView)
const isTestCaseManagementPage = computed(() => route.name === 'TestCaseManagement')

const menuGroups = [
  {
    title: '工作台',
    items: [
      { path: '/dashboard', label: '概览' },
      { path: '/projects', label: '项目管理' },
      { path: '/knowledge-management', label: '知识库' },
      { path: '/requirements', label: '需求评审' },
      { path: '/testcases', label: '用例管理' },
      { path: '/test-suites', label: '测试套件' },
      { path: '/test-executions', label: '执行历史' },
      { path: '/testcase-templates', label: '用例模板' },
      { path: '/ui-automation', label: 'UI自动化' },
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

watch(
  () => projectStore.currentProject?.id,
  (id) => {
    selectedProject.value = id
  }
)

onMounted(async () => {
  await projectStore.fetchProjects()
  selectedProject.value = projectStore.currentProject?.id
})

function navigate(path: string) {
  if (route.path === path || route.path.startsWith(path + '/')) return
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
    radial-gradient(900px 360px at 0% 0%, rgba(126, 224, 200, 0.18), transparent 55%),
    linear-gradient(180deg, #f4f7f6 0%, #e9f0ed 100%);
}

.header {
  height: 60px;
  display: flex;
  align-items: center;
  gap: 18px;
  padding: 0 20px;
  background: rgba(255, 255, 255, 0.88);
  border-bottom: 1px solid rgba(15, 61, 56, 0.07);
  backdrop-filter: blur(14px);
  z-index: 20;
}

.brand-block {
  display: flex;
  align-items: center;
  gap: 10px;
  cursor: pointer;
  min-width: 176px;
  border-radius: 10px;
  padding: 4px 6px 4px 2px;
  transition: background 0.18s ease;
}

.brand-block:hover,
.brand-block:focus-visible {
  background: rgba(15, 118, 110, 0.06);
  outline: none;
}

.brand-mark {
  width: 32px;
  height: 32px;
  border-radius: 9px;
  box-shadow: 0 4px 12px rgba(15, 61, 56, 0.14);
}

.brand-copy {
  display: flex;
  flex-direction: column;
  line-height: 1.15;
}

.brand {
  font-family: var(--kc-display);
  font-size: 17px;
  font-weight: 700;
  color: var(--kc-forest);
  letter-spacing: 0.01em;
}

.brand-sub {
  font-size: 10px;
  color: var(--theme-text-tertiary);
  letter-spacing: 0.1em;
  text-transform: uppercase;
}

.project-wrap {
  display: flex;
  align-items: center;
  gap: 8px;
}

.view-switch {
  margin-left: 8px;
}

.project-label {
  font-size: 12px;
  color: var(--theme-text-tertiary);
  white-space: nowrap;
}

.project-select {
  width: 220px;
}

.project-select :deep(.arco-select-view-single) {
  border-radius: 10px;
  background: #f7faf9;
  border-color: transparent;
}

.project-select :deep(.arco-select-view-single:hover),
.project-select :deep(.arco-select-view-focus) {
  background: #fff;
  border-color: rgba(15, 118, 110, 0.28);
}

.spacer {
  flex: 1;
}

.header-actions {
  display: flex;
  align-items: center;
  gap: 4px;
}

.user-chip {
  display: inline-flex;
  align-items: center;
  gap: 8px;
  padding: 5px 10px;
  border-radius: 999px;
  background: rgba(15, 61, 56, 0.05);
  color: var(--theme-text-secondary);
  font-size: 13px;
  max-width: 140px;
}

.user-name {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.user-dot {
  width: 7px;
  height: 7px;
  flex-shrink: 0;
  border-radius: 50%;
  background: #0f766e;
  box-shadow: 0 0 0 3px rgba(15, 118, 110, 0.14);
}

.logout-btn {
  color: var(--kc-forest) !important;
  border-radius: 8px !important;
}

.logout-btn:hover {
  background: rgba(15, 118, 110, 0.08) !important;
}

.body {
  min-height: calc(100vh - 60px);
}

.sider {
  background: rgba(255, 255, 255, 0.72);
  border-right: 1px solid rgba(15, 61, 56, 0.07);
  backdrop-filter: blur(10px);
}

.nav {
  padding: 16px 10px 28px;
  display: flex;
  flex-direction: column;
  gap: 16px;
}

.nav-group {
  display: flex;
  flex-direction: column;
  gap: 2px;
}

.nav-title {
  padding: 0 12px 6px;
  font-size: 11px;
  letter-spacing: 0.1em;
  text-transform: uppercase;
  color: #8aa098;
}

.nav-item {
  appearance: none;
  border: 0;
  background: transparent;
  text-align: left;
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 9px 12px;
  border-radius: 10px;
  color: #3d5a52;
  font-size: 14px;
  font-family: inherit;
  cursor: pointer;
  transition: background 0.16s ease, color 0.16s ease;
  white-space: nowrap;
  overflow: hidden;
}

.nav-item:hover {
  background: rgba(15, 118, 110, 0.07);
  color: var(--kc-forest);
}

.nav-item:focus-visible {
  outline: 2px solid rgba(15, 118, 110, 0.35);
  outline-offset: 1px;
}

.nav-item.active {
  background: rgba(15, 118, 110, 0.12);
  color: #0b5f59;
  font-weight: 600;
}

.nav-indicator {
  width: 6px;
  height: 6px;
  border-radius: 50%;
  background: currentColor;
  opacity: 0.35;
  flex-shrink: 0;
  transition: opacity 0.16s ease, transform 0.16s ease;
}

.nav-item.active .nav-indicator {
  opacity: 1;
  background: #0f766e;
  transform: scale(1.15);
}

.nav-label {
  overflow: hidden;
  text-overflow: ellipsis;
}

.content {
  padding: 16px 18px 18px;
  min-width: 0;
}

.content-frame {
  min-height: calc(100vh - 92px);
  height: calc(100vh - 92px);
  background: rgba(255, 255, 255, 0.78);
  border: 1px solid rgba(15, 61, 56, 0.06);
  border-radius: 18px;
  padding: 0;
  overflow: auto;
  box-shadow: 0 10px 28px rgba(15, 61, 56, 0.04);
  animation: rise-in 0.35s ease both;
}

@keyframes rise-in {
  from {
    opacity: 0;
    transform: translateY(6px);
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
  .project-select {
    width: 160px;
  }
  .content {
    padding: 10px;
  }
  .content-frame {
    border-radius: 14px;
    min-height: calc(100vh - 80px);
    height: calc(100vh - 80px);
  }
}
</style>
