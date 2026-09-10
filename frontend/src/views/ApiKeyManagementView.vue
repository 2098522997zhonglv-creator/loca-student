<template>
  <div class="apikey-management">
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
        <a-button type="primary" @click="showAddApiKeyModal">{{ pageText.createKey }}</a-button>
      </div>
    </div>

    <a-table
      :columns="columns"
      :data="apiKeyData"
      :pagination="pagination"
      :loading="loading"
      :scroll="{ x: 1200 }"
      @page-change="onPageChange"
      @page-size-change="onPageSizeChange"
    >
      <template #key="{ record }">
        <div class="key-display">
          <div class="key-content">
            <span v-if="record.key_visible">{{ record.key }}</span>
            <span v-else>{{ maskApiKey(record.key) }}</span>
          </div>
          <div class="key-actions">
            <a-tooltip :content="pageText.showOrHide">
              <a-button
                type="text"
                size="mini"
                @click="toggleKeyVisibility(record)"
              >
                <template #icon>
                  <icon-eye v-if="!record.key_visible" />
                  <icon-eye-invisible v-else />
                </template>
              </a-button>
            </a-tooltip>
            <a-tooltip :content="pageText.copy">
              <a-button
                type="text"
                size="mini"
                @click="copyApiKey(record.key)"
              >
                <template #icon><icon-copy /></template>
              </a-button>
            </a-tooltip>
          </div>
        </div>
      </template>
      <template #expires_at="{ record }">
        <span>{{ record.expires_at ? formatDate(record.expires_at) : pageText.neverExpires }}</span>
      </template>
      <template #is_active="{ record }">
        <a-tag :color="record.is_active ? 'green' : 'gray'">
          {{ record.is_active ? pageText.enabled : pageText.disabled }}
        </a-tag>
      </template>
      <template #operations="{ record }">
        <a-space>
          <a-button type="primary" size="small" @click="editApiKey(record)">{{ pageText.edit }}</a-button>
          <a-button type="primary" status="danger" size="small" @click="handleDelete(record)">{{ pageText.delete }}</a-button>
        </a-space>
      </template>
    </a-table>

    <!-- 添加/编辑 API Key 的模态框 -->
    <a-modal
      v-model:visible="apiKeyModalVisible"
      :title="isEditMode ? pageText.editKeyTitle : pageText.createKeyTitle"
      :ok-text="pageText.confirm"
      :cancel-text="pageText.cancel"
      @cancel="resetForm"
      @before-ok="handleSubmit"
    >
      <a-form
        ref="formRef"
        :model="formData"
        :rules="rules"
        label-align="left"
        :style="{ width: '100%' }"
        :label-col-props="{ span: 6 }"
        :wrapper-col-props="{ span: 18 }"
      >
        <a-form-item field="name" :label="pageText.name" validate-trigger="blur">
          <a-input v-model="formData.name" :placeholder="pageText.enterKeyName" />
        </a-form-item>
        <a-form-item field="expires_at" :label="pageText.expiresAt">
          <a-date-picker
            v-model="formData.expires_at"
            show-time
            format="YYYY-MM-DD HH:mm:ss"
            :placeholder="pageText.expiresAtPlaceholder"
            :show-confirm-btn="false"
            style="width: 100%"
          />
        </a-form-item>
        <a-form-item field="is_active" :label="pageText.status">
          <a-switch v-model="formData.is_active" />
        </a-form-item>
      </a-form>

      <div v-if="newApiKey" class="new-apikey-box">
        <div class="new-apikey-title">{{ pageText.newKeyCreated }}</div>
        <div class="new-apikey-content">
          <a-input-password
            v-model="newApiKey"
            :default-visible="true"
            readonly
            allow-clear
          />
        </div>
        <div class="new-apikey-actions">
          <a-button type="primary" size="small" @click="copyApiKey(newApiKey)">
            <template #icon><icon-copy /></template>
            {{ pageText.copy }}
          </a-button>
        </div>
      </div>
    </a-modal>

    <!-- 删除确认对话框 -->
    <a-modal
      v-model:visible="deleteModalVisible"
      :title="pageText.deleteConfirmTitle"
      :ok-text="pageText.confirm"
      :cancel-text="pageText.cancel"
      @ok="confirmDelete"
      simple
    >
      <p>{{ pageText.deleteConfirmContent(selectedApiKey?.name || '') }}</p>
    </a-modal>
  </div>
</template>

<script setup lang="ts">
import { ref, reactive, computed, watch } from 'vue';
import {
  Message,
  Modal,
  Form as AForm,
  FormItem as AFormItem,
  Input as AInput,
  InputPassword as AInputPassword,
  DatePicker as ADatePicker,
  Table as ATable,
  Button as AButton,
  Space as ASpace,
  Tag as ATag,
  Switch as ASwitch,
  Tooltip as ATooltip
} from '@arco-design/web-vue';
import { IconCopy, IconEye, IconEyeInvisible } from '@arco-design/web-vue/es/icon';
import {
  getApiKeyList,
  createApiKey as createApiKeyRequest,
  updateApiKey as updateApiKeyRequest,
  deleteApiKey as deleteApiKeyRequest,
  type ApiKey
} from '@/services/apiKeyService';
import { useProjectStore } from '@/store/projectStore';
import { useAppI18n } from '@/composables/useAppI18n';

const projectStore = useProjectStore();
const { isEnglish } = useAppI18n();
const pageText = computed(() => (
  isEnglish.value
    ? {
        searchPlaceholder: 'Search key name',
        createKey: 'Create key',
        showOrHide: 'Show/Hide',
        copy: 'Copy',
        neverExpires: 'Never expires',
        enabled: 'Enabled',
        disabled: 'Disabled',
        edit: 'Edit',
        delete: 'Delete',
        editKeyTitle: 'Edit key',
        createKeyTitle: 'Create key',
        confirm: 'Confirm',
        cancel: 'Cancel',
        name: 'Name',
        enterKeyName: 'Enter key name',
        expiresAt: 'Expires at',
        expiresAtPlaceholder: 'Select expiration time (leave empty for no expiration)',
        status: 'Status',
        newKeyCreated: 'Your new key has been created and will only be shown once',
        keyId: 'Key ID',
        keyName: 'Name',
        keyValue: 'Key',
        createdAt: 'Created at',
        actions: 'Actions',
        keyNameRequired: 'Key name is required',
        keyNameTooLong: 'Key name cannot exceed 100 characters',
        deleteSuccess: 'Deleted successfully',
        deleteFailed: 'Failed to delete',
        unknownError: 'Unknown error',
        keyCopied: 'Key copied to clipboard',
        copyFailed: 'Copy failed, please copy it manually',
        deleteConfirmTitle: 'Confirm deletion',
        deleteConfirmContent: (name: string) => `Delete key "${name}"? This action cannot be undone.`,
        confirmDeleteContent: (name: string) => `Delete key: ${name}?`,
        updateSuccess: 'Updated successfully',
        updateFailed: 'Failed to update',
        createSuccess: 'Created successfully',
        createFailed: 'Failed to create',
        createdButMissingFullKey: 'Key was created, but the full key could not be displayed',
        operationFailed: 'Operation failed',
        noKeyData: 'No key data',
        fetchKeyListFailed: 'Failed to fetch key list',
        keyVisibleWarning: 'Key is visible. Please keep it secure',
      }
    : {
        searchPlaceholder: '搜索Key名称',
        createKey: '创建Key',
        showOrHide: '显示/隐藏',
        copy: '复制',
        neverExpires: '永不过期',
        enabled: '启用',
        disabled: '禁用',
        edit: '编辑',
        delete: '删除',
        editKeyTitle: '编辑 Key',
        createKeyTitle: '创建 Key',
        confirm: '确认',
        cancel: '取消',
        name: '名称',
        enterKeyName: '请输入Key名称',
        expiresAt: '过期时间',
        expiresAtPlaceholder: '选择过期时间 (留空为永不过期)',
        status: '状态',
        newKeyCreated: '您的新Key已创建（仅显示一次）',
        keyId: 'Key ID',
        keyName: '名称',
        keyValue: 'Key',
        createdAt: '创建时间',
        actions: '操作',
        keyNameRequired: 'Key名称不能为空',
        keyNameTooLong: 'Key名称不能超过100个字符',
        deleteSuccess: '删除成功',
        deleteFailed: '删除失败',
        unknownError: '未知错误',
        keyCopied: 'Key已复制到剪贴板',
        copyFailed: '复制失败，请手动复制',
        deleteConfirmTitle: '删除确认',
        deleteConfirmContent: (name: string) => `确定要删除Key "${name}" 吗？此操作不可恢复。`,
        confirmDeleteContent: (name: string) => `确定要删除Key: ${name} 吗？`,
        updateSuccess: '更新成功',
        updateFailed: '更新失败',
        createSuccess: '创建成功',
        createFailed: '创建失败',
        createdButMissingFullKey: 'Key已创建，但无法显示完整的Key',
        operationFailed: '操作失败',
        noKeyData: '暂无Key数据',
        fetchKeyListFailed: '获取Key列表失败',
        keyVisibleWarning: 'Key已显示，请注意保护密钥安全',
      }
));

// API Key类型定义
interface ApiKeyItem {
  id: number;
  name: string;
  key: string;
  user: string; // 添加用户字段
  created_at: string;
  expires_at: string | null;
  is_active: boolean;
  key_visible?: boolean; // 控制是否显示完整key
}

// 加载状态
const loading = ref(false);
// 搜索关键词
const searchKeyword = ref('');

// 表格列定义
const columns = computed(() => [
  {
    title: pageText.value.keyId,
    dataIndex: 'id',
    width: 80,
    align: 'center',
  },
  {
    title: pageText.value.keyName,
    dataIndex: 'name',
    width: 150,
    align: 'center',
  },
  {
    title: pageText.value.keyValue,
    dataIndex: 'key',
    slotName: 'key',
    width: 200,
    align: 'center',
  },
  {
    title: pageText.value.createdAt,
    dataIndex: 'created_at',
    render: ({ record }: { record: ApiKeyItem }) => formatDate(record.created_at),
    width: 180,
    align: 'center',
  },
  {
    title: pageText.value.expiresAt,
    dataIndex: 'expires_at',
    slotName: 'expires_at',
    width: 180,
    align: 'center',
  },
  {
    title: pageText.value.status,
    dataIndex: 'is_active',
    slotName: 'is_active',
    width: 100,
    align: 'center',
  },
  {
    title: pageText.value.actions,
    slotName: 'operations',
    width: isEnglish.value ? 180 : 150,
    fixed: 'right',
    align: 'center',
  },
]);

// API Key 数据
const apiKeyData = ref<ApiKeyItem[]>([]);

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

// 表单相关
const formRef = ref();
const isEditMode = ref(false);
const apiKeyModalVisible = ref(false);
const formData = reactive({
  id: 0,
  name: '',
  expires_at: null as string | null,
  is_active: true,
});

// 表单验证规则
const rules = computed(() => ({
  name: [
    { required: true, message: pageText.value.keyNameRequired },
    { maxLength: 100, message: pageText.value.keyNameTooLong },
  ],
}));

// 删除相关
const deleteModalVisible = ref(false);
const selectedApiKey = ref<ApiKeyItem | null>(null);

// 确认删除
const confirmDelete = async () => {
  if (!selectedApiKey.value) return;
  
  loading.value = true;
  try {
    const deleteResponse = await deleteApiKeyRequest(selectedApiKey.value.id);
    if (deleteResponse.success) {
      Message.success(pageText.value.deleteSuccess);
      deleteModalVisible.value = false;
      selectedApiKey.value = null;
      fetchApiKeyList();
    } else {
      Message.error(deleteResponse.error || pageText.value.deleteFailed);
    }
  } catch (error: any) {
    console.error('删除Key错误:', error);
    Message.error(`${pageText.value.deleteFailed}: ${error.message || pageText.value.unknownError}`);
  } finally {
    loading.value = false;
  }
};

// 新创建的API Key（仅在创建成功后显示一次）
const newApiKey = ref<string>('');

// 日期格式化
const formatDate = (dateString: string) => {
  if (!dateString) return '-';
  const date = new Date(dateString);
  return date.toLocaleString(isEnglish.value ? 'en-US' : 'zh-CN', {
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
    hour12: false
  });
};

// 掩码显示API Key
const maskApiKey = (key: string) => {
  if (!key) return '';
  if (key.length <= 8) return '••••••••';
  return key.substring(0, 4) + '••••••••' + key.substring(key.length - 4);
};

// 复制Key到剪贴板（兼容HTTP环境）
const copyApiKey = async (key: string) => {
  try {
    // 优先使用 Clipboard API（HTTPS或localhost可用）
    if (navigator.clipboard && window.isSecureContext) {
      await navigator.clipboard.writeText(key);
      Message.success(pageText.value.keyCopied);
      return;
    }
    
    // 回退方案：使用 document.execCommand（兼容HTTP）
    const textArea = document.createElement('textarea');
    textArea.value = key;
    textArea.style.position = 'fixed';
    textArea.style.left = '-9999px';
    textArea.style.top = '-9999px';
    document.body.appendChild(textArea);
    textArea.focus();
    textArea.select();
    
    const successful = document.execCommand('copy');
    document.body.removeChild(textArea);
    
    if (successful) {
      Message.success(pageText.value.keyCopied);
    } else {
      Message.error(pageText.value.copyFailed);
    }
  } catch {
    Message.error(pageText.value.copyFailed);
  }
};

// 搜索
const onSearch = (value: string) => {
  searchKeyword.value = value;
  pagination.current = 1;
  fetchApiKeyList();
};

// 分页处理
const onPageChange = (page: number) => {
  pagination.current = page;
  fetchApiKeyList();
};

const onPageSizeChange = (pageSize: number) => {
  pagination.pageSize = pageSize;
  pagination.current = 1;
  fetchApiKeyList();
};

// 显示添加Key模态框
const showAddApiKeyModal = () => {
  isEditMode.value = false;
  resetForm();
  apiKeyModalVisible.value = true;
};

// 编辑Key
const editApiKey = (apiKey: ApiKeyItem) => {
  isEditMode.value = true;
  formData.id = apiKey.id;
  formData.name = apiKey.name;
  formData.expires_at = apiKey.expires_at;
  formData.is_active = apiKey.is_active;
  apiKeyModalVisible.value = true;
};

// 删除Key
const handleDelete = async (record: ApiKeyItem) => {
  Modal.confirm({
    title: pageText.value.deleteConfirmTitle,
    content: pageText.value.confirmDeleteContent(record.name),
    okText: pageText.value.delete,
    cancelText: pageText.value.cancel,
    onOk: async () => {
      loading.value = true;
      try {
        const deleteResponse = await deleteApiKeyRequest(record.id);
        console.log('删除Key响应:', deleteResponse);

        if (deleteResponse.success) {
          Message.success(pageText.value.deleteSuccess);
          fetchApiKeyList();
        } else {
          Message.error(deleteResponse.error || pageText.value.deleteFailed);
        }
      } catch (error: any) {
        console.error('删除Key错误:', error);
        Message.error(`${pageText.value.deleteFailed}: ${error.message || pageText.value.unknownError}`);
      } finally {
        loading.value = false;
      }
    }
  });
};

// 重置表单
const resetForm = () => {
  formData.id = 0;
  formData.name = '';
  formData.expires_at = null;
  formData.is_active = true;
  newApiKey.value = '';
  if (formRef.value) {
    formRef.value.resetFields();
  }
};

// 提交表单
const handleSubmit = async (done: (closed: boolean) => void) => {
  formRef.value.validate().then(async (errors: any) => {
    if (errors) {
      done(false);
      return;
    }

    loading.value = true;
    try {
      if (isEditMode.value) {
        // 更新Key
        const updateResponse = await updateApiKeyRequest(formData.id, {
          name: formData.name,
          expires_at: formData.expires_at,
          is_active: formData.is_active
        });
        console.log('更新Key响应:', updateResponse);

        if (updateResponse.success) {
          Message.success(pageText.value.updateSuccess);
          done(true);
          fetchApiKeyList();
        } else {
          Message.error(updateResponse.error || pageText.value.updateFailed);
          done(false);
        }
      } else {
        // 创建Key
        const createResponse = await createApiKeyRequest({
          name: formData.name,
          expires_at: formData.expires_at,
          is_active: formData.is_active
        });
        console.log('创建Key响应:', createResponse);

        if (createResponse.success && createResponse.data) {
          // 检查key是否存在于响应中
          if (createResponse.data.key) {
            newApiKey.value = createResponse.data.key;
            Message.success(pageText.value.createSuccess);
            done(true);
            fetchApiKeyList();
          } else {
            console.error('Key创建响应中缺少key字段:', createResponse.data);
            Message.warning(pageText.value.createdButMissingFullKey);
            done(true);
            fetchApiKeyList();
          }
        } else {
          Message.error(createResponse.error || pageText.value.createFailed);
          done(false);
        }
      }
    } catch (error: any) {
      console.error('Key操作错误:', error);
      Message.error(`${pageText.value.operationFailed}: ${error.message || pageText.value.unknownError}`);
      done(false);
    } finally {
      loading.value = false;
    }
  });
};

// 获取Key列表
const fetchApiKeyList = async () => {
  loading.value = true;
  try {
    // 使用apiKeyService获取数据
    const response = await getApiKeyList({
      page: pagination.current,
      pageSize: pagination.pageSize,
      search: searchKeyword.value
    });

    // 添加调试日志
    console.log('Key响应数据:', response);

    if (response.success) {
      // 后端返回的数据已经被request.ts处理过
      // 确保data是数组
      const dataList = Array.isArray(response.data) ? response.data : [];
      console.log('处理后的数据列表:', dataList);

      // 处理返回的数据
      apiKeyData.value = dataList.map(item => ({
        ...item,
        key_visible: false // 控制是否显示完整key
      }));
      pagination.total = response.total || dataList.length;

      // 如果数组为空，显示友好提示
      if (dataList.length === 0) {
        Message.info(pageText.value.noKeyData);
      }
    } else {
      console.error('API响应不成功:', response);
      Message.error(response.error || pageText.value.fetchKeyListFailed);
      apiKeyData.value = [];
      pagination.total = 0;
    }
  } catch (error: any) {
    // 解析错误消息
    const errorMessage = error.message || pageText.value.unknownError;
    console.error('Key列表获取错误:', error);
    Message.error(`${pageText.value.fetchKeyListFailed}: ${errorMessage}`);
    apiKeyData.value = [];
    pagination.total = 0;
  } finally {
    loading.value = false;
  }
};

// 监听项目变化，重新加载数据
watch(() => projectStore.currentProjectId, (newProjectId, oldProjectId) => {
  if (newProjectId !== oldProjectId) {
    // 项目切换时重置状态
    pagination.current = 1;
    searchKeyword.value = '';

    // 重新获取API Key列表
    fetchApiKeyList();
  }
}, { immediate: false });

// 初始化加载数据
fetchApiKeyList();

// 切换Key可见性
const toggleKeyVisibility = (record: ApiKeyItem) => {
  record.key_visible = !record.key_visible;

  // 如果显示了Key，提示用户注意安全
  if (record.key_visible) {
    Message.warning(pageText.value.keyVisibleWarning);

    // 10秒后自动隐藏
    setTimeout(() => {
      record.key_visible = false;
    }, 10000);
  }
};
</script>

<style scoped>
.apikey-management {
  padding: 20px;
}

.page-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 16px;
}

.new-apikey-box {
  margin-top: 16px;
  padding: 16px;
  background-color: #f2f3f5;
  border-radius: 4px;
}

.new-apikey-title {
  font-weight: bold;
  margin-bottom: 8px;
  color: #f5222d;
}

.new-apikey-content {
  margin-bottom: 8px;
}

.new-apikey-actions {
  display: flex;
  justify-content: flex-end;
}

.key-display {
  display: flex;
  align-items: center;
  justify-content: space-between;
  width: 100%;
}

.key-content {
  font-family: monospace;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  max-width: 300px;
}

.key-actions {
  display: flex;
  align-items: center;
  white-space: nowrap;
}
</style>
