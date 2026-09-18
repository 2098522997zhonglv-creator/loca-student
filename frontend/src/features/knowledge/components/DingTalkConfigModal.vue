<template>
  <a-modal
    :visible="visible"
    title="钉钉同步配置"
    :width="560"
    @ok="handleSubmit"
    @cancel="handleCancel"
    :confirm-loading="loading"
  >
    <a-spin :loading="fetchLoading">
      <a-alert type="info" style="margin-bottom: 16px">
        需企业管理员在钉钉开放平台创建企业内部应用，开通 Wiki / 文档只读权限后填写下方凭证。
      </a-alert>
      <a-form ref="formRef" :model="formData" layout="vertical">
        <a-form-item label="启用钉钉同步" field="enabled">
          <a-switch v-model="formData.enabled" />
        </a-form-item>
        <a-form-item label="Client ID / AppKey" field="app_key" required>
          <a-input v-model="formData.app_key" placeholder="开放平台应用凭证中的 Client ID" />
        </a-form-item>
        <a-form-item label="Client Secret / AppSecret" field="app_secret" required>
          <a-input-password v-model="formData.app_secret" placeholder="Client Secret" />
        </a-form-item>
        <a-form-item label="操作人 User ID" field="operator_user_id" required>
          <a-input
            v-model="formData.operator_user_id"
            placeholder="企业管理后台通讯录中的 User ID"
          />
        </a-form-item>
        <a-form-item v-if="formData.operator_union_id" label="unionId（自动解析）">
          <a-input :model-value="formData.operator_union_id" disabled />
        </a-form-item>
        <a-form-item>
          <a-button type="outline" :loading="testing" @click="handleTest">
            测试连接
          </a-button>
        </a-form-item>
        <div v-if="testResult" class="test-result">
          <a-alert :type="testResult.ok ? 'success' : 'error'">
            <template v-if="testResult.ok">
              连接成功，可见知识库 {{ testResult.workspace_count ?? 0 }} 个
            </template>
            <template v-else>
              {{ testResult.error || '连接失败' }}
            </template>
          </a-alert>
          <ul v-if="testResult.ok && testResult.workspaces?.length" class="workspace-list">
            <li v-for="ws in testResult.workspaces" :key="ws.workspace_id || ws.name">
              {{ ws.name || '未命名' }}
              <span class="muted">（{{ ws.workspace_id }}）</span>
            </li>
          </ul>
        </div>
      </a-form>
    </a-spin>
  </a-modal>
</template>

<script setup lang="ts">
import { reactive, ref, watch } from 'vue';
import { Message } from '@arco-design/web-vue';
import { KnowledgeService } from '../services/knowledgeService';
import type { DingTalkConfig } from '../types/knowledge';

const props = defineProps<{ visible: boolean }>();
const emit = defineEmits<{ submit: []; cancel: [] }>();

const formRef = ref();
const loading = ref(false);
const fetchLoading = ref(false);
const testing = ref(false);
const formData = reactive({
  enabled: false,
  app_key: '',
  app_secret: '',
  operator_user_id: '',
  operator_union_id: '',
});
const testResult = ref<{
  ok: boolean;
  error?: string;
  workspace_count?: number;
  workspaces?: Array<{ workspace_id?: string; name?: string; root_node_id?: string }>;
} | null>(null);

const loadConfig = async () => {
  fetchLoading.value = true;
  testResult.value = null;
  try {
    const cfg = await KnowledgeService.getDingTalkConfig();
    Object.assign(formData, {
      enabled: !!cfg.enabled,
      app_key: cfg.app_key || '',
      app_secret: cfg.app_secret || '',
      operator_user_id: cfg.operator_user_id || '',
      operator_union_id: cfg.operator_union_id || '',
    });
  } catch (e: any) {
    Message.error(e?.message || '加载钉钉配置失败');
  } finally {
    fetchLoading.value = false;
  }
};

watch(
  () => props.visible,
  (v) => {
    if (v) loadConfig();
  },
);

const handleTest = async () => {
  testing.value = true;
  testResult.value = null;
  try {
    const result = await KnowledgeService.testDingTalkConnection({
      enabled: true,
      app_key: formData.app_key,
      app_secret: formData.app_secret,
      operator_user_id: formData.operator_user_id,
    });
    testResult.value = result;
    if (result.ok) {
      Message.success('钉钉连接测试成功');
      if (result.operator_union_id) {
        formData.operator_union_id = result.operator_union_id;
      }
    } else {
      Message.error(result.error || '连接失败');
    }
  } catch (e: any) {
    testResult.value = { ok: false, error: e?.message || '连接失败' };
    Message.error(e?.message || '连接失败');
  } finally {
    testing.value = false;
  }
};

const handleSubmit = async () => {
  loading.value = true;
  try {
    await KnowledgeService.updateDingTalkConfig({
      enabled: formData.enabled,
      app_key: formData.app_key,
      app_secret: formData.app_secret,
      operator_user_id: formData.operator_user_id,
    } as Partial<DingTalkConfig>);
    Message.success('钉钉配置已保存');
    emit('submit');
  } catch (e: any) {
    Message.error(e?.message || '保存失败');
  } finally {
    loading.value = false;
  }
};

const handleCancel = () => emit('cancel');
</script>

<style scoped>
.test-result {
  margin-top: 8px;
}
.workspace-list {
  margin: 8px 0 0;
  padding-left: 18px;
  max-height: 160px;
  overflow: auto;
  font-size: 13px;
}
.muted {
  color: var(--color-text-3);
}
</style>
