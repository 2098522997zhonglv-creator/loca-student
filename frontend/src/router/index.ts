import { createRouter, createWebHistory, type RouteRecordRaw } from 'vue-router'
import { useAuthStore } from '@/store/authStore'
import MainLayout from '@/layouts/MainLayout.vue'
import LoginView from '@/views/LoginView.vue'
import RegisterView from '@/views/RegisterView.vue'
import KnowledgeHomeView from '@/views/KnowledgeHomeView.vue'
import ProjectManagementView from '@/views/ProjectManagementView.vue'
import UserManagementView from '@/views/UserManagementView.vue'
import OrganizationManagementView from '@/views/OrganizationManagementView.vue'
import PermissionManagementView from '@/views/PermissionManagementView.vue'
import KnowledgeManagementView from '@/features/knowledge/views/KnowledgeManagementView.vue'
import RequirementManagementView from '@/features/requirements/views/RequirementManagementView.vue'
import DocumentDetailView from '@/features/requirements/views/DocumentDetailView.vue'
import SpecializedReportView from '@/features/requirements/views/SpecializedReportView.vue'
import LlmConfigManagementView from '@/features/langgraph/views/LlmConfigManagementView.vue'
import LangGraphChatView from '@/features/langgraph/views/LangGraphChatView.vue'
import FileManagementView from '@/features/file-management/views/FileManagementView.vue'
import ApiKeyManagementView from '@/views/ApiKeyManagementView.vue'
import RemoteMcpConfigManagementView from '@/views/RemoteMcpConfigManagementView.vue'
import SkillsManagementView from '@/features/skills/views/SkillsManagementView.vue'
import TestCaseManagementView from '@/views/TestCaseManagementView.vue'
import TestSuiteManagementView from '@/views/TestSuiteManagementView.vue'
import TestExecutionHistoryView from '@/views/TestExecutionHistoryView.vue'
import TemplateManagementView from '@/features/testcase-templates/views/TemplateManagementView.vue'
import UiAutomationView from '@/features/ui-automation/views/UiAutomationView.vue'
import OperationLogView from '@/views/OperationLogView.vue'
import SystemRuntimeLogView from '@/views/SystemRuntimeLogView.vue'

const children: RouteRecordRaw[] = [
  { path: 'dashboard', name: 'Dashboard', component: KnowledgeHomeView },
  { path: 'projects', name: 'ProjectManagement', component: ProjectManagementView },
  { path: 'requirements', name: 'RequirementManagement', component: RequirementManagementView },
  { path: 'requirements/:id', name: 'DocumentDetail', component: DocumentDetailView },
  { path: 'requirements/:id/report', name: 'ReportDetail', component: SpecializedReportView },
  { path: 'knowledge-management', name: 'KnowledgeManagement', component: KnowledgeManagementView },
  { path: 'langgraph-chat', name: 'LangGraphChat', component: LangGraphChatView },
  { path: 'file-management', name: 'FileManagement', component: FileManagementView },
  { path: 'testcases', name: 'TestCaseManagement', component: TestCaseManagementView },
  { path: 'test-suites', name: 'TestSuiteManagement', component: TestSuiteManagementView },
  { path: 'test-executions', name: 'TestExecutionHistory', component: TestExecutionHistoryView },
  { path: 'testcase-templates', name: 'TemplateManagement', component: TemplateManagementView },
  { path: 'ui-automation', name: 'UiAutomation', component: UiAutomationView },
  {
    path: 'ui-automation/trace/:id',
    name: 'UiAutomationTraceDetail',
    component: () => import('@/features/ui-automation/views/TraceDetail.vue'),
  },
  { path: 'users', name: 'UserManagement', component: UserManagementView },
  { path: 'organizations', name: 'OrganizationManagement', component: OrganizationManagementView },
  { path: 'permissions', name: 'PermissionManagement', component: PermissionManagementView },
  { path: 'llm-configs', name: 'LlmConfigManagement', component: LlmConfigManagementView },
  { path: 'api-keys', name: 'ApiKeyManagement', component: ApiKeyManagementView },
  { path: 'remote-mcp-configs', name: 'RemoteMcpConfigManagement', component: RemoteMcpConfigManagementView },
  { path: 'skills', name: 'SkillsManagement', component: SkillsManagementView },
  { path: 'operation-logs', name: 'OperationLogs', component: OperationLogView },
  { path: 'system-runtime-logs', name: 'SystemRuntimeLogs', component: SystemRuntimeLogView },
]

const routes: RouteRecordRaw[] = [
  { path: '/login', name: 'Login', component: LoginView },
  { path: '/register', name: 'Register', component: RegisterView },
  { path: '/', component: MainLayout, meta: { requiresAuth: true }, redirect: '/dashboard', children },
  { path: '/:pathMatch(.*)*', redirect: '/dashboard' },
]

const router = createRouter({ history: createWebHistory(import.meta.env.BASE_URL), routes })
router.beforeEach((to) => {
  const auth = useAuthStore()
  auth.checkAuthStatus()
  const publicRoute = to.name === 'Login' || to.name === 'Register'
  if (!auth.isAuthenticated && !publicRoute) return { name: 'Login', query: { redirect: to.fullPath } }
  if (auth.isAuthenticated && publicRoute) return { name: 'Dashboard' }
})
export default router
