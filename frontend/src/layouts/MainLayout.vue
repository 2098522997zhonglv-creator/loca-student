<template>
  <a-layout class="shell">
    <a-layout-header class="header">
      <div class="brand">本地知识中心</div>
      <a-select v-model="selectedProject" placeholder="选择项目" style="width: 220px" @change="changeProject">
        <a-option v-for="item in projectStore.projectOptions" :key="item.value" :value="item.value">{{ item.label }}</a-option>
      </a-select>
      <div class="spacer" />
      <span>{{ auth.user?.username }}</span>
      <a-button @click="logout">退出</a-button>
    </a-layout-header>
    <a-layout>
      <a-layout-sider :width="190" class="sider">
        <a-menu :selected-keys="[active]" @menu-item-click="navigate">
          <a-menu-item v-for="item in menus" :key="item.path">{{ item.label }}</a-menu-item>
        </a-menu>
      </a-layout-sider>
      <a-layout-content class="content"><router-view /></a-layout-content>
    </a-layout>
  </a-layout>
</template>

<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useAuthStore } from '@/store/authStore'
import { useProjectStore } from '@/store/projectStore'

const router = useRouter()
const route = useRoute()
const auth = useAuthStore()
const projectStore = useProjectStore()
const selectedProject = ref<number | undefined>(projectStore.currentProject?.id)
const menus = [
  { path: '/dashboard', label: '概览' }, { path: '/projects', label: '项目管理' },
  { path: '/knowledge-management', label: '知识库' }, { path: '/requirements', label: '需求评审' },
  { path: '/langgraph-chat', label: '知识问答' }, { path: '/file-management', label: '文件管理' },
  { path: '/llm-configs', label: 'LLM 配置' }, { path: '/users', label: '用户管理' },
  { path: '/organizations', label: '组织管理' }, { path: '/permissions', label: '权限管理' },
  { path: '/api-keys', label: 'API Key' }, { path: '/remote-mcp-configs', label: 'MCP 配置' },
  { path: '/skills', label: 'Skills' }, { path: '/operation-logs', label: '操作日志' },
  { path: '/system-runtime-logs', label: '运行日志' },
]
const active = computed(() => '/' + route.path.split('/')[1])
onMounted(async () => {
  await projectStore.fetchProjects()
  selectedProject.value = projectStore.currentProject?.id
})
function navigate(path: string) { router.push(path) }
function changeProject(value: string | number) {
  const project = projectStore.projectList.find(item => item.id === Number(value))
  if (project) projectStore.setCurrentProject(project)
}
async function logout() { auth.logout(); await router.push('/login') }
</script>

<style scoped>
.shell { min-height: 100vh; background: #f5f7fa; }
.header { height: 58px; display: flex; align-items: center; gap: 22px; padding: 0 24px; background: #fff; border-bottom: 1px solid #e5e6eb; }
.brand { font-size: 19px; font-weight: 700; color: #165dff; }
.spacer { flex: 1; }
.sider { background: #fff; border-right: 1px solid #e5e6eb; }
.content { padding: 20px; min-width: 0; }
</style>
