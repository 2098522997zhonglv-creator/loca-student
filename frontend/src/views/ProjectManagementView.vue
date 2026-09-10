<template>
  <div class="project-management">
    <!-- 无项目权限时显示提示 -->
    <div v-if="!hasProjectPermission" class="no-permission-panel">
      <div class="no-permission-content">
        <div class="icon-container">
          <svg class="permission-icon" viewBox="0 0 24 24" fill="none" xmlns="http://www.w3.org/2000/svg">
            <path d="M12 2C6.48 2 2 6.48 2 12s4.48 10 10 10 10-4.48 10-10S17.52 2 12 2zm1 15h-2v-2h2v2zm0-4h-2V7h2v6z" fill="#f5222d"/>
          </svg>
        </div>
        <h3>{{ pageText.restrictedTitle }}</h3>
        <p>{{ pageText.restrictedDescription }}</p>
        <a-button type="primary" @click="contactAdmin">{{ pageText.contactAdmin }}</a-button>
      </div>
    </div>

    <!-- 有权限时显示正常内容 -->
    <div v-else>
      <div class="page-header">
        <div class="search-box">
          <a-input-search
            :placeholder="pageText.searchPlaceholder"
            allow-clear
            style="width: 300px"
            @search="onSearch"
          />
        </div>
        <div class="action-buttons">
          <a-button type="primary" @click="showAddProjectModal">{{ pageText.addProject }}</a-button>
        </div>
      </div>

      <a-table
        :key="`project-table-${locale}`"
        :columns="columns"
        :data="projectData"
        :pagination="pagination"
        :loading="loading"
        :scroll="{ x: 900 }"
        @page-change="onPageChange"
        @page-size-change="onPageSizeChange"
        @row-click="handleRowClick"
      >
        <template #name="{ record }">
          <a-tooltip :content="record.name" position="top">
            <div class="ellipsis-text">{{ record.name }}</div>
          </a-tooltip>
        </template>
        <template #description="{ record }">
          <a-tooltip :content="record.description || pageText.noDescription" position="top">
            <div class="ellipsis-text">{{ record.description || pageText.noDescription }}</div>
          </a-tooltip>
        </template>
        <template #operations="{ record }">
          <a-space class="project-operations" :size="[4, 4]">
            <a-button type="primary" size="mini" @click="viewProjectMembers(record, $event)">{{ pageText.members }}</a-button>
            <a-button type="primary" size="mini" @click="editProject(record, $event)">{{ pageText.edit }}</a-button>
            <a-button type="primary" status="danger" size="mini" @click="deleteProject(record, $event)">{{ pageText.delete }}</a-button>
          </a-space>
        </template>
        <template #empty>
          <a-empty :description="pageText.noProjectsData" />
        </template>
      </a-table>
    </div>

    <!-- 项目成员管理模态框 -->
    <a-modal
      v-model:visible="membersModalVisible"
      :title="membersModalTitle"
      :footer="false"
      :mask-closable="true"
      :width="900"
    >
      <div v-if="membersLoading" class="loading-container">
        <a-spin />
      </div>
      <div v-else>
        <div class="members-header">
          <a-button type="primary" @click="showAddMemberModal">{{ pageText.addMember }}</a-button>
        </div>
        <div v-if="projectMembers.length === 0" class="no-data">
          {{ pageText.noMembersData }}
        </div>
        <a-table
          v-else
          :key="`project-members-table-${locale}`"
          :columns="memberColumns"
          :data="projectMembers"
          :pagination="false"
          row-key="id"
          :scroll="{ x: 760 }"
        >
          <template #role="{ record }">
            <a-tag :color="record.role === 'owner' ? 'red' : record.role === 'admin' ? 'orange' : 'blue'">
              {{ getRoleLabel(record.role) }}
            </a-tag>
          </template>
          <template #joined_at="{ record }">
            {{ record.joined_at ? formatDateTime(record.joined_at) : '-' }}
          </template>
          <template #operations="{ record }">
            <a-space class="member-operations" :size="[4, 4]">
              <a-button
                type="text"
                size="mini"
                @click="showUpdateRoleModal(record)"
                :disabled="record.role === 'owner'"
              >
                {{ pageText.updateRole }}
              </a-button>
              <a-button
                type="text"
                size="mini"
                status="danger"
                @click="removeMember(record)"
                :disabled="record.role === 'owner'"
              >
                {{ pageText.remove }}
              </a-button>
            </a-space>
          </template>
        </a-table>

      </div>
    </a-modal>

    <!-- 添加成员模态框 -->
    <a-modal
      v-model:visible="addMemberModalVisible"
      :title="pageText.addProjectMemberTitle"
      :ok-text="pageText.confirm"
      :cancel-text="pageText.cancel"
      @ok="handleAddMember"
      @cancel="() => addMemberModalVisible = false"
      :mask-closable="false"
    >
      <a-form :model="addMemberForm" layout="vertical">
        <a-form-item field="userId" :label="pageText.selectUser" required>
          <a-select
            v-model="addMemberForm.userId"
            :placeholder="pageText.selectUserPlaceholder"
            :loading="usersLoading"
            :filter-option="true"
          >
            <a-option
              v-for="user in availableUsers"
              :key="user.value"
              :value="user.value"
              :label="user.label"
            />
          </a-select>
        </a-form-item>
        <a-form-item field="role" :label="pageText.role" required>
          <a-select v-model="addMemberForm.role" :placeholder="pageText.selectRolePlaceholder">
            <a-option value="member">{{ pageText.memberRole }}</a-option>
            <a-option value="admin">{{ pageText.adminRole }}</a-option>
            <a-option value="owner">{{ pageText.ownerRole }}</a-option>
          </a-select>
        </a-form-item>
      </a-form>
    </a-modal>

    <!-- 更新角色模态框 -->
    <a-modal
      v-model:visible="updateRoleModalVisible"
      :title="pageText.updateMemberRoleTitle"
      :ok-text="pageText.confirm"
      :cancel-text="pageText.cancel"
      @ok="handleUpdateRole"
      @cancel="() => updateRoleModalVisible = false"
      :mask-closable="false"
    >
      <a-form :model="updateRoleForm" layout="vertical">
        <a-form-item field="role" :label="pageText.role" required>
          <a-select v-model="updateRoleForm.role" :placeholder="pageText.selectRolePlaceholder">
            <a-option value="member">{{ pageText.memberRole }}</a-option>
            <a-option value="admin">{{ pageText.adminRole }}</a-option>
            <a-option value="owner">{{ pageText.ownerRole }}</a-option>
          </a-select>
        </a-form-item>
      </a-form>
    </a-modal>

    <!-- 添加项目模态框 -->
    <a-modal
      v-model:visible="addProjectModalVisible"
      :title="pageText.addProjectTitle"
      :ok-text="pageText.confirm"
      :cancel-text="pageText.cancel"
      @ok="handleAddProject"
      @cancel="cancelAddProject"
      :mask-closable="false"
      width="700px"
    >
      <a-form
        ref="addProjectFormRef"
        :model="addProjectForm"
        :rules="addProjectRules"
        layout="vertical"
      >
        <a-form-item field="name" :label="pageText.projectName">
          <a-input v-model="addProjectForm.name" :placeholder="pageText.enterProjectName" />
        </a-form-item>
        <a-form-item field="description" :label="pageText.projectDescription">
          <a-textarea
            v-model="addProjectForm.description"
            :placeholder="pageText.enterProjectDescription"
            :auto-size="{ minRows: 3, maxRows: 5 }"
          />
        </a-form-item>
        
        <!-- 凭据列表 -->
        <a-divider orientation="left">{{ pageText.projectCredentials }}</a-divider>
        <div v-for="(credential, index) in addProjectForm.credentials" :key="index" style="margin-bottom: 8px; display: flex; align-items: flex-start; gap: 8px;">
          <a-input v-model="credential.system_url" :placeholder="pageText.projectUrl" style="flex: 2;" />
          <a-input v-model="credential.username" :placeholder="pageText.username" style="flex: 1;" />
          <a-input-password v-model="credential.password" :placeholder="pageText.password" style="flex: 1;" />
          <a-input v-model="credential.user_role" :placeholder="pageText.role" style="flex: 1;" />
          <a-button type="text" status="danger" @click="removeCredential(index)">{{ pageText.delete }}</a-button>
        </div>
        <a-button type="dashed" long @click="addCredential">
          <template #icon>
            <icon-plus />
          </template>
          {{ pageText.addCredential }}
        </a-button>
      </a-form>
    </a-modal>

    <!-- 编辑项目模态框 -->
    <a-modal
      v-model:visible="editProjectModalVisible"
      :title="pageText.editProjectTitle"
      :ok-text="pageText.confirm"
      :cancel-text="pageText.cancel"
      @ok="handleEditProject"
      @cancel="cancelEditProject"
      :mask-closable="false"
      width="700px"
    >
      <a-form
        ref="editProjectFormRef"
        :model="editProjectForm"
        :rules="editProjectRules"
        layout="vertical"
      >
        <a-form-item field="name" :label="pageText.projectName">
          <a-input v-model="editProjectForm.name" :placeholder="pageText.enterProjectName" />
        </a-form-item>
        <a-form-item field="description" :label="pageText.projectDescription">
          <a-textarea
            v-model="editProjectForm.description"
            :placeholder="pageText.enterProjectDescription"
            :auto-size="{ minRows: 3, maxRows: 5 }"
          />
        </a-form-item>
        
        <!-- 凭据列表 -->
        <a-divider orientation="left">{{ pageText.projectCredentials }}</a-divider>
        <div v-for="(credential, index) in editProjectForm.credentials" :key="index" style="margin-bottom: 8px; display: flex; align-items: flex-start; gap: 8px;">
          <a-input v-model="credential.system_url" :placeholder="pageText.projectUrl" style="flex: 2;" />
          <a-input v-model="credential.username" :placeholder="pageText.username" style="flex: 1;" />
          <a-input-password v-model="credential.password" :placeholder="pageText.keepEmptyPassword" style="flex: 1;" />
          <a-input v-model="credential.user_role" :placeholder="pageText.role" style="flex: 1;" />
          <a-button type="text" status="danger" @click="removeEditCredential(index)">{{ pageText.delete }}</a-button>
        </div>
        <a-button type="dashed" long @click="addEditCredential">
          <template #icon>
            <icon-plus />
          </template>
          {{ pageText.addCredential }}
        </a-button>
      </a-form>
    </a-modal>
  </div>
</template>

<script setup lang="ts">
import { ref, reactive, onMounted, watch, computed } from 'vue';
import { Message, Modal } from '@arco-design/web-vue';
import {
  getProjectList,
  createProject,
  deleteProject as deleteProjectService,
  updateProject,
  getProjectDetail,
  getProjectMembers,
  addProjectMember,
  removeProjectMember,
  updateProjectMemberRole,
  type Project,
  type ProjectMember,
  type CreateProjectRequest,
  type UpdateProjectRequest
} from '@/services/projectService';
import { getUserList } from '@/services/userService';
import { useAuthStore } from '@/store/authStore';
import { useAppI18n } from '@/composables/useAppI18n';

// 加载状态
const loading = ref(false);
// 搜索关键词
const searchKeyword = ref('');

// 权限检查
const authStore = useAuthStore();
const { locale, isEnglish } = useAppI18n();
const hasProjectPermission = ref(false); // 默认无权限，等待权限检查结果

const pageText = computed(() => (
  isEnglish.value
    ? {
        restrictedTitle: 'Access restricted',
        restrictedDescription: 'You do not currently have project management permission. Contact an administrator to request access.',
        contactAdmin: 'Contact admin',
        contactAdminInfo: 'Contact the system administrator to get project management permission',
        searchPlaceholder: 'Search project name / description',
        addProject: 'Add project',
        noDescription: 'No description',
        noProjectsData: 'No project data',
        members: 'Members',
        edit: 'Edit',
        delete: 'Delete',
        projectMembersManagement: 'Project members',
        addMember: 'Add member',
        noMembersData: 'No member data',
        ownerRole: 'Owner',
        adminRole: 'Admin',
        memberRole: 'Member',
        updateRole: 'Update role',
        remove: 'Remove',
        addProjectMemberTitle: 'Add project member',
        selectUser: 'User',
        selectUserPlaceholder: 'Select a user',
        role: 'Role',
        selectRolePlaceholder: 'Select a role',
        updateMemberRoleTitle: 'Update member role',
        addProjectTitle: 'Add project',
        editProjectTitle: 'Edit project',
        projectName: 'Project name',
        enterProjectName: 'Enter project name',
        projectDescription: 'Project description',
        enterProjectDescription: 'Enter project description',
        projectCredentials: 'Project credentials',
        projectUrl: 'Project URL',
        username: 'Username',
        password: 'Password',
        keepEmptyPassword: 'Leave blank to keep unchanged',
        addCredential: 'Add credential',
        projectId: 'Project ID',
        creator: 'Created by',
        createdAt: 'Created at',
        operations: 'Actions',
        userId: 'User ID',
        email: 'Email',
        joinedAt: 'Joined at',
        fetchProjectListFailed: 'Failed to fetch project list',
        fetchProjectListError: 'An error occurred while fetching the project list',
        fetchProjectDetailFailed: 'Failed to fetch project details',
        fetchProjectDetailError: 'An error occurred while fetching project details',
        fetchProjectMembersFailed: 'Failed to fetch project members',
        fetchProjectMembersError: 'An error occurred while fetching project members',
        fetchUsersFailed: 'Failed to fetch users',
        fetchUsersError: 'An error occurred while fetching users',
        noProjectSelected: 'No project selected',
        noProjectOrMemberSelected: 'No project or member selected',
        noUserSelected: 'Select a user',
        memberAddedSuccess: 'Member added successfully',
        addMemberFailed: 'Failed to add member',
        addMemberError: 'An error occurred while adding the member',
        confirmRemoveTitle: 'Confirm removal',
        confirmRemoveMemberContent: (username: string) => `Remove member "${username}"?`,
        memberRemovedSuccess: 'Member removed successfully',
        removeMemberFailed: 'Failed to remove member',
        removeMemberError: 'An error occurred while removing the member',
        roleUpdatedSuccess: 'Role updated successfully',
        updateRoleFailed: 'Failed to update role',
        updateRoleError: 'An error occurred while updating the role',
        confirm: 'Confirm',
        cancel: 'Cancel',
        projectNameRequired: 'Enter project name',
        projectNameMax: 'Project name must be at most 100 characters',
        projectDescriptionMax: 'Project description must be at most 500 characters',
        projectCreatedSuccess: 'Project created successfully',
        createProjectFailed: 'Failed to create project',
        createProjectError: 'An error occurred while creating the project',
        projectUpdatedSuccess: 'Project updated successfully',
        updateProjectFailed: 'Failed to update project',
        updateProjectError: 'An error occurred while updating the project',
        confirmDeleteTitle: 'Confirm deletion',
        confirmDeleteProjectContent: (name: string) => `Delete project "${name}"? This action cannot be undone.`,
        projectDeletedSuccess: 'Project deleted successfully',
        deleteProjectFailed: 'Failed to delete project',
        deleteProjectError: 'An error occurred while deleting the project',
      }
    : {
        restrictedTitle: '访问受限',
        restrictedDescription: '您当前没有项目管理权限，请联系系统管理员申请相应权限。',
        contactAdmin: '联系管理员',
        contactAdminInfo: '请联系系统管理员获取项目管理权限',
        searchPlaceholder: '搜索项目名称/描述',
        addProject: '添加项目',
        noDescription: '无描述',
        noProjectsData: '暂无项目数据',
        members: '成员',
        edit: '编辑',
        delete: '删除',
        projectMembersManagement: '项目成员管理',
        addMember: '添加成员',
        noMembersData: '暂无成员数据',
        ownerRole: '拥有者',
        adminRole: '管理员',
        memberRole: '成员',
        updateRole: '修改角色',
        remove: '移除',
        addProjectMemberTitle: '添加项目成员',
        selectUser: '选择用户',
        selectUserPlaceholder: '请选择用户',
        role: '角色',
        selectRolePlaceholder: '请选择角色',
        updateMemberRoleTitle: '更新成员角色',
        addProjectTitle: '添加项目',
        editProjectTitle: '编辑项目',
        projectName: '项目名称',
        enterProjectName: '请输入项目名称',
        projectDescription: '项目描述',
        enterProjectDescription: '请输入项目描述',
        projectCredentials: '项目凭据',
        projectUrl: '项目地址',
        username: '用户名',
        password: '密码',
        keepEmptyPassword: '留空不改',
        addCredential: '添加凭据',
        projectId: '项目ID',
        creator: '创建者',
        createdAt: '创建时间',
        operations: '操作',
        userId: '用户ID',
        email: '邮箱',
        joinedAt: '加入时间',
        fetchProjectListFailed: '获取项目列表失败',
        fetchProjectListError: '获取项目列表时发生错误',
        fetchProjectDetailFailed: '获取项目详情失败',
        fetchProjectDetailError: '获取项目详情时发生错误',
        fetchProjectMembersFailed: '获取项目成员列表失败',
        fetchProjectMembersError: '获取项目成员列表时发生错误',
        fetchUsersFailed: '获取用户列表失败',
        fetchUsersError: '获取用户列表时发生错误',
        noProjectSelected: '未选择项目',
        noProjectOrMemberSelected: '未选择项目或成员',
        noUserSelected: '请选择用户',
        memberAddedSuccess: '成员添加成功',
        addMemberFailed: '添加成员失败',
        addMemberError: '添加成员时发生错误',
        confirmRemoveTitle: '确认移除',
        confirmRemoveMemberContent: (username: string) => `确定要移除成员 "${username}" 吗？`,
        memberRemovedSuccess: '成员移除成功',
        removeMemberFailed: '移除成员失败',
        removeMemberError: '移除成员时发生错误',
        roleUpdatedSuccess: '角色更新成功',
        updateRoleFailed: '更新角色失败',
        updateRoleError: '更新角色时发生错误',
        confirm: '确定',
        cancel: '取消',
        projectNameRequired: '请输入项目名称',
        projectNameMax: '项目名称长度不能超过100个字符',
        projectDescriptionMax: '项目描述长度不能超过500个字符',
        projectCreatedSuccess: '项目创建成功',
        createProjectFailed: '创建项目失败',
        createProjectError: '创建项目时发生错误',
        projectUpdatedSuccess: '项目更新成功',
        updateProjectFailed: '更新项目失败',
        updateProjectError: '更新项目时发生错误',
        confirmDeleteTitle: '确认删除',
        confirmDeleteProjectContent: (name: string) => `确定要删除项目 "${name}" 吗？此操作不可恢复。`,
        projectDeletedSuccess: '项目删除成功',
        deleteProjectFailed: '删除项目失败',
        deleteProjectError: '删除项目时发生错误',
      }
));

const formatDateTime = (dateValue: string) => (
  new Date(dateValue).toLocaleString(isEnglish.value ? 'en-US' : 'zh-CN')
);

const projectIdColumnWidth = computed(() => (isEnglish.value ? 110 : 80));
const projectOperationsColumnWidth = computed(() => (isEnglish.value ? 230 : 180));
const memberOperationsColumnWidth = computed(() => (isEnglish.value ? 190 : 150));

const getRoleLabel = (role: string) => {
  if (role === 'owner') return pageText.value.ownerRole;
  if (role === 'admin') return pageText.value.adminRole;
  return pageText.value.memberRole;
};

// 简单的权限检查逻辑
const checkProjectPermission = () => {
  // 如果用户未登录，则无权限
  if (!authStore.isAuthenticated || !authStore.user) {
    hasProjectPermission.value = false;
    return;
  }
  
  // 如果是管理员，拥有所有权限
  if (authStore.user?.is_staff) {
    hasProjectPermission.value = true;
    return;
  }
  
  // 检查是否有项目相关权限
  const projectPermissions = [
    'projects.view_project',
    'projects.add_project',  
    'projects.change_project',
    'projects.delete_project'
  ];
  
  // 使用authStore的hasPermission方法检查权限
  hasProjectPermission.value = projectPermissions.some(permission =>
    authStore.hasPermission(permission)
  );
};

// 联系管理员
const contactAdmin = () => {
  Message.info(pageText.value.contactAdminInfo);
};

// 表格列定义
const columns = computed(() => [
  {
    title: pageText.value.projectId,
    dataIndex: 'id',
    width: projectIdColumnWidth.value,
  },
  {
    title: pageText.value.projectName,
    dataIndex: 'name',
    slotName: 'name',
    width: 200,
  },
  {
    title: pageText.value.projectDescription,
    dataIndex: 'description',
    slotName: 'description',
    width: 300,
  },
  {
    title: pageText.value.creator,
    dataIndex: 'creator_detail',
    render: ({ record }: { record: Project }) => {
      return record.creator_detail?.username || '-';
    }
  },
  {
    title: pageText.value.createdAt,
    dataIndex: 'created_at',
    render: ({ record }: { record: Project }) => formatDateTime(record.created_at),
  },
  {
    title: pageText.value.operations,
    slotName: 'operations',
    width: projectOperationsColumnWidth.value,
    fixed: 'right',
  },
]);

// 项目数据
const projectData = ref<Project[]>([]);

// 分页配置
const pagination = reactive({
  total: 0,
  current: 1,
  pageSize: 10,
  showTotal: true,
  showJumper: true,
  showPageSize: true,
  pageSizeOptions: [10, 20, 50, 100],
});

// 获取项目列表
const fetchProjectList = async () => {
  loading.value = true;
  try {
    const response = await getProjectList({
      page: pagination.current,
      pageSize: pagination.pageSize,
      search: searchKeyword.value,
    });

    if (response.success && response.data) {
      projectData.value = response.data;
      pagination.total = response.total || response.data.length;
    } else {
      Message.error(response.error || pageText.value.fetchProjectListFailed);
      projectData.value = [];
      pagination.total = 0;
    }
  } catch (error) {
    console.error('获取项目列表出错:', error);
    Message.error(pageText.value.fetchProjectListError);
    projectData.value = [];
    pagination.total = 0;
  } finally {
    loading.value = false;
  }
};

// 搜索项目
const onSearch = (value: string) => {
  searchKeyword.value = value;
  pagination.current = 1; // 重置到第一页
  fetchProjectList();
};

// 分页变化
const onPageChange = (page: number) => {
  pagination.current = page;
  fetchProjectList();
};

// 每页显示数量变化
const onPageSizeChange = (pageSize: number) => {
  pagination.pageSize = pageSize;
  pagination.current = 1; // 重置到第一页
  fetchProjectList();
};

// 在组件挂载时检查权限并加载项目数据
onMounted(() => {
  checkProjectPermission();
  fetchProjectList();
});

// 监听认证状态变化
watch(() => authStore.isAuthenticated, () => {
  checkProjectPermission();
}, { immediate: true });

// 监听用户权限变化
watch(() => authStore.userPermissions, () => {
  checkProjectPermission();
}, { deep: true });

// 处理行点击事件
const handleRowClick = (record: Project) => {
  console.log('点击了项目:', record);
  // 查看项目详情
  viewProjectDetail(record);
};

// 项目成员管理相关
const membersModalVisible = ref(false);
const membersLoading = ref(false);
const selectedProject = ref<Project | null>(null);
const projectMembers = ref<ProjectMember[]>([]);
const membersModalTitle = computed(() => (
  `${pageText.value.projectMembersManagement}: ${selectedProject.value?.name || ''}`
));

// 项目成员表格列定义
const memberColumns = computed(() => [
  {
    title: pageText.value.userId,
    dataIndex: 'user',
    width: 80,
  },
  {
    title: pageText.value.username,
    dataIndex: 'user_detail.username',
    width: 120,
  },
  {
    title: pageText.value.email,
    dataIndex: 'user_detail.email',
    width: 180,
  },
  {
    title: pageText.value.role,
    dataIndex: 'role',
    slotName: 'role',
    width: 80,
  },
  {
    title: pageText.value.joinedAt,
    dataIndex: 'joined_at',
    slotName: 'joined_at',
    width: 180,
  },
  {
    title: pageText.value.operations,
    slotName: 'operations',
    width: memberOperationsColumnWidth.value,
    fixed: 'right',
  }
]);

// 监听项目成员数据变化
watch(projectMembers, () => {
  // 成员数据已更新
}, { deep: true });

// 查看项目详情
const viewProjectDetail = async (project: Project) => {
  try {
    const response = await getProjectDetail(project.id);
    if (response.success && response.data) {
      console.log('项目详情:', response.data);
      // 可以在这里添加更多处理逻辑
    } else {
      Message.error(response.error || pageText.value.fetchProjectDetailFailed);
    }
  } catch (error) {
    console.error('获取项目详情出错:', error);
    Message.error(pageText.value.fetchProjectDetailError);
  }
};

// 查看项目成员
const viewProjectMembers = async (project: Project, event?: Event) => {
  // 阻止事件冒泡，避免触发行点击事件
  if (event) {
    event.stopPropagation();
  }

  selectedProject.value = project;
  membersModalVisible.value = true;
  await fetchProjectMembers(project.id);
};

// 获取项目成员列表
const fetchProjectMembers = async (projectId: number) => {
  membersLoading.value = true;
  try {
    const response = await getProjectMembers(projectId);
    if (response.success && response.data) {

      projectMembers.value = response.data;
    } else {
      Message.error(response.error || pageText.value.fetchProjectMembersFailed);
      projectMembers.value = [];
    }
  } catch (error) {
    console.error('获取项目成员列表出错:', error);
    Message.error(pageText.value.fetchProjectMembersError);
    projectMembers.value = [];
  } finally {
    membersLoading.value = false;
  }
};

// 添加成员模态框相关
const addMemberModalVisible = ref(false);
const addMemberForm = reactive({
  userId: undefined as number | undefined,
  role: 'member' as string
});
const availableUsers = ref<{ label: string; value: number }[]>([]);
const usersLoading = ref(false);

// 显示添加成员模态框
const showAddMemberModal = () => {
  addMemberForm.userId = undefined;
  addMemberForm.role = 'member';
  addMemberModalVisible.value = true;
  fetchAvailableUsers();
};

// 获取可用用户列表
const fetchAvailableUsers = async () => {
  usersLoading.value = true;
  try {
    const response = await getUserList({
      page: 1,
      pageSize: 100, // 获取较多用户
    });

    if (response.success && response.data) {
      // 过滤掉已经是项目成员的用户
      const memberUserIds = projectMembers.value.map(member => member.user);
      const filteredUsers = response.data.filter(user => !memberUserIds.includes(user.id));

      // 转换为下拉选择框需要的格式
      availableUsers.value = filteredUsers.map(user => ({
        label: `${user.username} (${user.email || ''})`,
        value: user.id
      }));
    } else {
      Message.error(response.error || pageText.value.fetchUsersFailed);
      availableUsers.value = [];
    }
  } catch (error) {
    console.error('获取用户列表出错:', error);
    Message.error(pageText.value.fetchUsersError);
    availableUsers.value = [];
  } finally {
    usersLoading.value = false;
  }
};

// 处理添加成员
const handleAddMember = async () => {
  if (!selectedProject.value) {
    Message.error(pageText.value.noProjectSelected);
    return;
  }

  if (!addMemberForm.userId) {
    Message.error(pageText.value.noUserSelected);
    return;
  }

  try {
    const response = await addProjectMember(
      selectedProject.value.id,
      addMemberForm.userId,
      addMemberForm.role
    );

    if (response.success) {
      Message.success(pageText.value.memberAddedSuccess);
      addMemberModalVisible.value = false;
      // 刷新成员列表
      await fetchProjectMembers(selectedProject.value.id);
    } else {
      Message.error(response.error || pageText.value.addMemberFailed);
    }
  } catch (error) {
    console.error('添加成员出错:', error);
    Message.error(pageText.value.addMemberError);
  }
};

// 移除成员
const removeMember = (member: ProjectMember) => {
  if (!selectedProject.value) {
    Message.error(pageText.value.noProjectSelected);
    return;
  }



  Modal.warning({
    title: pageText.value.confirmRemoveTitle,
    content: pageText.value.confirmRemoveMemberContent(member.user_detail?.username || ''),
    okText: pageText.value.remove,
    cancelText: pageText.value.cancel,
    onOk: async () => {
      try {
        const response = await removeProjectMember(selectedProject.value!.id, member.user);

        if (response.success) {
          Message.success(pageText.value.memberRemovedSuccess);
          // 刷新成员列表
          await fetchProjectMembers(selectedProject.value!.id);
        } else {
          Message.error(response.error || pageText.value.removeMemberFailed);
        }
      } catch (error) {
        console.error('移除成员出错:', error);
        Message.error(pageText.value.removeMemberError);
      }
    }
  });
};

// 更新成员角色模态框相关
const updateRoleModalVisible = ref(false);
const selectedMember = ref<ProjectMember | null>(null);
const updateRoleForm = reactive({
  role: ''
});

// 显示更新角色模态框
const showUpdateRoleModal = (member: ProjectMember) => {
  selectedMember.value = member;
  updateRoleForm.role = member.role;
  updateRoleModalVisible.value = true;
};

// 处理更新角色
const handleUpdateRole = async () => {
  if (!selectedProject.value || !selectedMember.value) {
    Message.error(pageText.value.noProjectOrMemberSelected);
    return;
  }

  try {
    const response = await updateProjectMemberRole(
      selectedProject.value.id,
      selectedMember.value.user,
      updateRoleForm.role
    );

    if (response.success) {
      Message.success(pageText.value.roleUpdatedSuccess);
      updateRoleModalVisible.value = false;
      // 刷新成员列表
      await fetchProjectMembers(selectedProject.value.id);
    } else {
      Message.error(response.error || pageText.value.updateRoleFailed);
    }
  } catch (error) {
    console.error('更新角色出错:', error);
    Message.error(pageText.value.updateRoleError);
  }
};

// 添加项目模态框相关
const addProjectModalVisible = ref(false);
const addProjectFormRef = ref();
const addProjectForm = reactive<CreateProjectRequest>({
  name: '',
  description: '',
  credentials: []
});

// 添加项目表单验证规则
const addProjectRules = computed(() => ({
  name: [
    { required: true, message: pageText.value.projectNameRequired },
    { maxLength: 100, message: pageText.value.projectNameMax }
  ],
  description: [
    { maxLength: 500, message: pageText.value.projectDescriptionMax }
  ]
}));

// 添加凭据
const addCredential = () => {
  if (!addProjectForm.credentials) {
    addProjectForm.credentials = [];
  }
  addProjectForm.credentials.push({
    system_url: '',
    username: '',
    password: '',
    user_role: ''
  });
};

// 删除凭据
const removeCredential = (index: number) => {
  if (addProjectForm.credentials) {
    addProjectForm.credentials.splice(index, 1);
  }
};

// 显示添加项目模态框
const showAddProjectModal = () => {
  // 重置表单
  addProjectForm.name = '';
  addProjectForm.description = '';
  addProjectForm.credentials = [];
  // 默认添加一个凭据
  addCredential();
  // 显示模态框
  addProjectModalVisible.value = true;
};

// 取消添加项目
const cancelAddProject = () => {
  addProjectModalVisible.value = false;
};

// 处理添加项目
const handleAddProject = async () => {
  // 验证表单
  try {
    await addProjectFormRef.value.validate();
  } catch (errors) {
    console.error('表单验证失败:', errors);
    return;
  }

  // 创建项目数据对象
  const projectData: CreateProjectRequest = {
    name: addProjectForm.name,
    description: addProjectForm.description,
    credentials: addProjectForm.credentials || []
  };

  try {
    const response = await createProject(projectData);

    if (response.success) {
      Message.success(pageText.value.projectCreatedSuccess);
      fetchProjectList();
      // 关闭模态框
      addProjectModalVisible.value = false;
    } else {
      Message.error(response.error || pageText.value.createProjectFailed);
    }
  } catch (error) {
    console.error('创建项目出错:', error);
    Message.error(pageText.value.createProjectError);
  }
};

// 编辑项目模态框相关
const editProjectModalVisible = ref(false);
const editProjectFormRef = ref();
const editProjectForm = reactive<UpdateProjectRequest & { id: number }>({
  id: 0,
  name: '',
  description: '',
  credentials: []
});

// 编辑项目表单验证规则
const editProjectRules = computed(() => ({
  name: [
    { required: true, message: pageText.value.projectNameRequired },
    { maxLength: 100, message: pageText.value.projectNameMax }
  ],
  description: [
    { maxLength: 500, message: pageText.value.projectDescriptionMax }
  ]
}));

// 编辑凭据
const addEditCredential = () => {
  if (!editProjectForm.credentials) {
    editProjectForm.credentials = [];
  }
  editProjectForm.credentials.push({
    system_url: '',
    username: '',
    password: '',
    user_role: ''
  });
};

const removeEditCredential = (index: number) => {
  if (editProjectForm.credentials) {
    editProjectForm.credentials.splice(index, 1);
  }
};

// 显示编辑项目模态框
const editProject = (project: Project, event?: Event) => {
  if (event) {
    event.stopPropagation();
  }
  
  // 设置表单数据
  editProjectForm.id = project.id;
  editProjectForm.name = project.name;
  editProjectForm.description = project.description;
  
  // 复制凭据数据（密码不显示）
  editProjectForm.credentials = (project.credentials || []).map(cred => ({
    id: cred.id,
    system_url: cred.system_url,
    username: cred.username,
    password: '',
    user_role: cred.user_role
  }));

  editProjectModalVisible.value = true;
};

// 取消编辑项目
const cancelEditProject = () => {
  editProjectModalVisible.value = false;
};

// 处理编辑项目
const handleEditProject = async () => {
  try {
    await editProjectFormRef.value.validate();
  } catch (errors) {
    console.error('表单验证失败:', errors);
    return;
  }

  // 更新项目数据对象
  const projectData: UpdateProjectRequest = {
    name: editProjectForm.name,
    description: editProjectForm.description,
    credentials: editProjectForm.credentials || []
  };

  try {
    const response = await updateProject(editProjectForm.id, projectData);

    if (response.success) {
      Message.success(pageText.value.projectUpdatedSuccess);
      fetchProjectList();
      editProjectModalVisible.value = false;
    } else {
      Message.error(response.error || pageText.value.updateProjectFailed);
    }
  } catch (error) {
    console.error('更新项目出错:', error);
    Message.error(pageText.value.updateProjectError);
  }
};

// 删除项目
const deleteProject = (project: Project, event?: Event) => {
  // 阻止事件冒泡，避免触发行点击事件
  if (event) {
    event.stopPropagation();
  }

  Modal.warning({
    title: pageText.value.confirmDeleteTitle,
    content: pageText.value.confirmDeleteProjectContent(project.name),
    okText: pageText.value.delete,
    cancelText: pageText.value.cancel,
    onOk: async () => {
      try {
        const response = await deleteProjectService(project.id);
        if (response.success) {
          Message.success(pageText.value.projectDeletedSuccess);
          // 刷新项目列表
          fetchProjectList();
        } else {
          Message.error(response.error || pageText.value.deleteProjectFailed);
        }
      } catch (error) {
        console.error('删除项目出错:', error);
        Message.error(pageText.value.deleteProjectError);
      }
    }
  });
};
</script>

<style scoped>
.project-management {
  background-color: #fff;
  border-radius: 8px;
  padding: 20px;
  box-shadow: 0 0 10px rgba(0, 0, 0, 0.15);
  height: 100%;
  box-sizing: border-box;
}

.page-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 20px;
}

.action-buttons {
  display: flex;
  gap: 10px;
}

.loading-container {
  display: flex;
  justify-content: center;
  align-items: center;
  height: 200px;
}

.members-header {
  display: flex;
  justify-content: flex-end;
  margin-bottom: 16px;
}

.no-data {
  display: flex;
  justify-content: center;
  align-items: center;
  padding: 40px 0;
}

.no-permission-panel {
  display: flex;
  justify-content: center;
  align-items: center;
  min-height: 400px;
  padding: 40px 20px;
}

.no-permission-content {
  text-align: center;
  background: #f9f9f9;
  padding: 40px 32px;
  border-radius: 12px;
  border: 1px solid #e8e8e8;
  max-width: 400px;
  width: 100%;
}

.icon-container {
  margin-bottom: 20px;
}

.permission-icon {
  width: 48px;
  height: 48px;
  opacity: 0.8;
}

.no-permission-content h3 {
  font-size: 18px;
  font-weight: 600;
  color: #262626;
  margin-bottom: 12px;
  margin-top: 0;
}

.no-permission-content p {
  font-size: 14px;
  color: #595959;
  margin-bottom: 24px;
  line-height: 1.5;
}

/* 文本省略样式 */
.ellipsis-text {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  width: 100%;
  display: block;
  box-sizing: border-box;
}

/* 确保表格单元格也遵循省略规则 */
:deep(.arco-table-td) {
  overflow: hidden;
}

:deep(.arco-table-cell) {
  overflow: hidden;
}

/* 强制表格使用固定布局以确保列宽度生效 */
:deep(.arco-table-container .arco-table-element) {
  table-layout: fixed;
}

/* 确保tooltip内容换行显示 */
:deep(.arco-tooltip-content-inner) {
  max-width: 300px;
  white-space: normal;
  word-break: break-word;
}

/* 操作按钮样式优化 */
:deep(.arco-table-th.operations-header) {
  white-space: nowrap;
}

:deep(.arco-table-td.operations-cell) {
  padding: 8px 4px;
}

:deep(.project-operations.arco-space),
:deep(.member-operations.arco-space) {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  justify-content: flex-start;
  gap: 4px;
}

:deep(.project-operations.arco-space .arco-space-item),
:deep(.member-operations.arco-space .arco-space-item) {
  margin-right: 0 !important;
}

:deep(.project-operations .arco-btn),
:deep(.member-operations .arco-btn) {
  white-space: nowrap;
}

:deep(.arco-btn-size-mini) {
  padding: 0 8px;
  font-size: 12px;
  height: 24px;
  line-height: 22px;
}

/* 确保操作列按钮不溢出 */
:deep(.arco-space-item) {
  margin-right: 2px !important;
}

:deep(.arco-space-item:last-child) {
  margin-right: 0 !important;
}

/* 成员管理表格样式优化 */
:deep(.arco-table-th) {
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

:deep(.arco-table-th-title) {
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

/* 操作按钮区域样式 */
:deep(.arco-table-td.operations-cell) {
  padding: 8px 4px;
}

:deep(.arco-space-item) {
  margin-right: 4px !important;
}

:deep(.arco-space-item:last-child) {
  margin-right: 0 !important;
}

/* 确保表格在模态框中的宽度合适 */
:deep(.arco-modal-body) {
  max-height: 70vh;
  overflow-y: auto;
}

:deep(.arco-table-container) {
  overflow-x: auto;
}
</style>
