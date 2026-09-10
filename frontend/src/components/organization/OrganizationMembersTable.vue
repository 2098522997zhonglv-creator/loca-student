<template>
  <div class="organization-members">
    <div class="table-header">
      <div class="search-box">
        <a-input-search
          :placeholder="text.searchPlaceholder"
          allow-clear
          style="width: 300px"
          @search="onSearch"
        />
      </div>
      <div class="action-buttons">
        <a-button type="primary" @click="showAddMembersModal">{{ text.addMembers }}</a-button>
      </div>
    </div>

    <a-table
      :columns="columns"
      :data="membersData"
      :pagination="pagination"
      :loading="loading"
      @page-change="onPageChange"
      @page-size-change="onPageSizeChange"
    >
      <template #operations="{ record }">
        <a-space>
          <a-button type="primary" status="danger" size="small" @click="removeMember(record)">{{ text.remove }}</a-button>
        </a-space>
      </template>
    </a-table>

    <!-- 添加成员模态框 -->
    <a-modal
      v-model:visible="addMembersModalVisible"
      :title="text.addMembersTitle"
      @cancel="cancelAddMembers"
      @before-ok="handleAddMembers"
      :mask-closable="false"
      :width="600"
    >
      <a-form ref="addMembersFormRef" :model="addMembersForm" layout="vertical" :auto-label-width="false">
        <a-form-item field="userIds" :label="text.selectUsers" required>
          <a-select
            v-model="addMembersForm.userIds"
            :placeholder="text.selectUsersPlaceholder"
            multiple
            :options="availableUsers"
            :filter-option="true"
            :max-tag-count="5"
          />
        </a-form-item>
      </a-form>
    </a-modal>
  </div>
</template>

<script setup lang="ts">
import { ref, reactive, onMounted, watch, computed } from 'vue';
import { Message, Modal } from '@arco-design/web-vue';
import { useAppI18n } from '@/composables/useAppI18n';
import {
  getOrganizationUsers,
  addUsersToOrganization,
  removeUsersFromOrganization,
  type OrganizationUser
} from '@/services/organizationService';
import { getUserList } from '@/services/userService';

const props = defineProps<{
  organizationId: number;
}>();

const emit = defineEmits<{
  (e: 'refresh'): void;
}>();
const { isEnglish } = useAppI18n();
const text = computed(() => (
  isEnglish.value
    ? {
        searchPlaceholder: 'Search username/email',
        addMembers: 'Add members',
        remove: 'Remove',
        addMembersTitle: 'Add members',
        selectUsers: 'Select users',
        selectUsersPlaceholder: 'Select users',
        userId: 'User ID',
        username: 'Username',
        email: 'Email',
        fullName: 'Full name',
        actions: 'Actions',
        fetchMembersFailed: 'Failed to fetch organization members',
        fetchMembersError: 'An error occurred while fetching organization members',
        fetchUsersFailed: 'Failed to fetch user list',
        fetchUsersError: 'An error occurred while fetching user list',
        invalidOrganizationId: 'Invalid organization ID',
        selectAtLeastOneUser: 'Select at least one user',
        addMembersSuccess: 'Members added successfully',
        addMembersFailed: 'Failed to add members',
        addMembersError: 'An error occurred while adding members',
        removeConfirmTitle: 'Confirm removal',
        removeConfirmContent: (username: string) => `Remove user "${username}" from this organization?`,
        removeConfirmOk: 'Remove',
        cancel: 'Cancel',
        removeMembersSuccess: 'Member removed successfully',
        removeMembersFailed: 'Failed to remove member',
        removeMembersError: 'An error occurred while removing the member',
      }
    : {
        searchPlaceholder: '搜索用户名/邮箱',
        addMembers: '添加成员',
        remove: '移除',
        addMembersTitle: '添加成员',
        selectUsers: '选择用户',
        selectUsersPlaceholder: '请选择用户',
        userId: '用户ID',
        username: '用户名',
        email: '邮箱',
        fullName: '姓名',
        actions: '操作',
        fetchMembersFailed: '获取组织成员列表失败',
        fetchMembersError: '获取组织成员列表时发生错误',
        fetchUsersFailed: '获取用户列表失败',
        fetchUsersError: '获取用户列表时发生错误',
        invalidOrganizationId: '组织ID无效',
        selectAtLeastOneUser: '请选择至少一个用户',
        addMembersSuccess: '成员添加成功',
        addMembersFailed: '添加成员失败',
        addMembersError: '添加成员时发生错误',
        removeConfirmTitle: '确认移除',
        removeConfirmContent: (username: string) => `确定要将用户 "${username}" 从组织中移除吗？`,
        removeConfirmOk: '确定移除',
        cancel: '取消',
        removeMembersSuccess: '成员移除成功',
        removeMembersFailed: '移除成员失败',
        removeMembersError: '移除成员时发生错误',
      }
));

// 加载状态
const loading = ref(false);
// 搜索关键词
const searchKeyword = ref('');

// 表格列定义
const columns = computed(() => [
  {
    title: text.value.userId,
    dataIndex: 'id',
    width: 80,
  },
  {
    title: text.value.username,
    dataIndex: 'username',
  },
  {
    title: text.value.email,
    dataIndex: 'email',
  },
  {
    title: text.value.fullName,
    dataIndex: 'name',
    render: ({ record }: { record: OrganizationUser }) => {
      const fullName = [record.first_name, record.last_name].filter(Boolean).join(' ');
      return fullName || '-';
    },
  },
  {
    title: text.value.actions,
    slotName: 'operations',
    width: 100,
  },
]);

// 成员数据
const membersData = ref<OrganizationUser[]>([]);

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

// 获取组织成员列表数据
const fetchMembersList = async () => {
  if (!props.organizationId) return;

  loading.value = true;
  try {
    const response = await getOrganizationUsers(props.organizationId, {
      page: pagination.current,
      pageSize: pagination.pageSize,
      search: searchKeyword.value
    });

    if (response.success && response.data) {
      membersData.value = response.data;
      pagination.total = response.total || response.data.length;
    } else {
      Message.error(response.error || text.value.fetchMembersFailed);
      membersData.value = [];
      pagination.total = 0;
    }
  } catch (error) {
    console.error('获取组织成员列表出错:', error);
    Message.error(text.value.fetchMembersError);
    membersData.value = [];
    pagination.total = 0;
  } finally {
    loading.value = false;
  }
};

// 搜索成员
const onSearch = (value: string) => {
  searchKeyword.value = value;
  pagination.current = 1; // 重置到第一页
  fetchMembersList();
};

// 分页变化
const onPageChange = (page: number) => {
  pagination.current = page;
  fetchMembersList();
};

// 每页显示数量变化
const onPageSizeChange = (pageSize: number) => {
  pagination.pageSize = pageSize;
  pagination.current = 1; // 重置到第一页
  fetchMembersList();
};

// 在组件挂载时加载成员数据
onMounted(() => {
  if (props.organizationId) {
    fetchMembersList();
  }
});

// 监听组织ID变化
watch(() => props.organizationId, (newId) => {
  if (newId) {
    pagination.current = 1; // 重置到第一页
    fetchMembersList();
  }
});

// 可用用户列表
const availableUsers = ref<{ label: string; value: number }[]>([]);

// 获取所有用户列表
const fetchAllUsers = async () => {
  try {
    const response = await getUserList({
      page: 1,
      pageSize: 100, // 获取较多用户
    });

    if (response.success && response.data) {
      // 过滤掉已经是组织成员的用户
      const memberIds = membersData.value.map(member => member.id);
      const filteredUsers = response.data.filter(user => !memberIds.includes(user.id));

      // 转换为下拉选择框需要的格式
      availableUsers.value = filteredUsers.map(user => ({
        label: `${user.username} (${user.email})`,
        value: user.id
      }));
    } else {
      Message.error(response.error || text.value.fetchUsersFailed);
      availableUsers.value = [];
    }
  } catch (error) {
    console.error('获取用户列表出错:', error);
    Message.error(text.value.fetchUsersError);
    availableUsers.value = [];
  }
};

// 添加成员模态框相关
const addMembersModalVisible = ref(false);
const addMembersFormRef = ref();
const addMembersForm = reactive({
  userIds: [] as number[]
});

// 显示添加成员模态框
const showAddMembersModal = async () => {
  // 重置表单
  addMembersForm.userIds = [];

  // 获取可用用户列表
  await fetchAllUsers();

  // 显示模态框
  addMembersModalVisible.value = true;
};

// 取消添加成员
const cancelAddMembers = () => {
  addMembersModalVisible.value = false;
};

// 处理添加成员
const handleAddMembers = async (done: (closed: boolean) => void) => {
  if (!props.organizationId) {
    Message.error(text.value.invalidOrganizationId);
    done(false);
    return;
  }

  if (addMembersForm.userIds.length === 0) {
    Message.warning(text.value.selectAtLeastOneUser);
    done(false);
    return;
  }

  try {
    const response = await addUsersToOrganization(props.organizationId, addMembersForm.userIds);

    if (response.success) {
      Message.success(response.message || text.value.addMembersSuccess);
      // 刷新成员列表
      fetchMembersList();
      // 通知父组件刷新
      emit('refresh');
      done(true); // 关闭模态框
    } else {
      Message.error(response.error || text.value.addMembersFailed);
      done(false); // 不关闭模态框
    }
  } catch (error) {
    console.error('添加成员出错:', error);
    Message.error(text.value.addMembersError);
    done(false); // 不关闭模态框
  }
};

// 移除成员
const removeMember = (member: OrganizationUser) => {
  if (!props.organizationId) {
    Message.error(text.value.invalidOrganizationId);
    return;
  }

  Modal.warning({
    title: text.value.removeConfirmTitle,
    content: text.value.removeConfirmContent(member.username),
    okText: text.value.removeConfirmOk,
    cancelText: text.value.cancel,
    onOk: async () => {
      try {
        const response = await removeUsersFromOrganization(props.organizationId, [member.id]);

        if (response.success) {
          Message.success(response.message || text.value.removeMembersSuccess);
          // 刷新成员列表
          fetchMembersList();
          // 通知父组件刷新
          emit('refresh');
        } else {
          Message.error(response.error || text.value.removeMembersFailed);
        }
      } catch (error) {
        console.error('移除成员出错:', error);
        Message.error(text.value.removeMembersError);
      }
    }
  });
};
</script>

<style scoped>
.organization-members {
  margin-top: 20px;
}

.table-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 20px;
}

.search-box {
  display: flex;
  align-items: center;
}

.action-buttons {
  display: flex;
  gap: 10px;
}
</style>
