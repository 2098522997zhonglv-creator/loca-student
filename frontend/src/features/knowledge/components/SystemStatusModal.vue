<template>
  <a-modal
    :visible="visible"
    :title="text.modalTitle"
    :width="800"
    @cancel="handleClose"
    :footer="false"
  >
    <div v-if="loading" class="loading-container">
      <a-spin size="large" />
      <p>{{ text.loading }}</p>
    </div>

    <div v-else-if="error" class="error-container">
      <a-result status="error" :title="error" />
      <div class="retry-container">
        <a-button type="primary" @click="fetchSystemStatus">
          {{ text.retry }}
        </a-button>
      </div>
    </div>

    <div v-else-if="systemStatus" class="status-container">
      <!-- 总体状态 -->
      <div class="status-header">
        <a-tag
          :color="systemStatus.overall_status === 'healthy' ? 'green' : 'red'"
          size="large"
        >
          {{ systemStatus.overall_status === 'healthy' ? text.systemHealthy : text.systemUnhealthy }}
        </a-tag>
        <span class="timestamp">
          {{ text.checkedAt }}{{ formatTimestamp(systemStatus.timestamp) }}
        </span>
      </div>

      <!-- 嵌入模型状态 -->
      <div class="status-section">
        <h3>{{ text.embeddingStatus }}</h3>
        <div class="model-info">
          <div class="info-item">
            <span class="label">{{ text.modelName }}</span>
            <span class="value">{{ systemStatus.embedding_model.model_name }}</span>
          </div>
          <div class="info-item">
            <span class="label">{{ text.status }}</span>
            <a-tag :color="systemStatus.embedding_model.status === 'working' ? 'green' : 'red'">
              {{ systemStatus.embedding_model.status === 'working' ? text.normal : text.abnormal }}
            </a-tag>
          </div>
          <div class="info-item">
            <span class="label">{{ text.modelExists }}</span>
            <a-tag :color="systemStatus.embedding_model.model_exists ? 'green' : 'red'">
              {{ systemStatus.embedding_model.model_exists ? text.yes : text.no }}
            </a-tag>
          </div>
          <div class="info-item">
            <span class="label">{{ text.loadTest }}</span>
            <a-tag :color="systemStatus.embedding_model.load_test ? 'green' : 'red'">
              {{ systemStatus.embedding_model.load_test ? text.passed : text.failed }}
            </a-tag>
          </div>
          <div class="info-item">
            <span class="label">{{ text.vectorDimension }}</span>
            <span class="value">{{ systemStatus.embedding_model.dimension }}</span>
          </div>
          <div class="info-item">
            <span class="label">{{ text.cachePath }}</span>
            <span class="value small">{{ systemStatus.embedding_model.cache_path }}</span>
          </div>
        </div>
      </div>

      <!-- 依赖状态 -->
      <div class="status-section">
        <h3>{{ text.dependencies }}</h3>
        <div class="dependencies-grid">
          <div
            v-for="(status, name) in systemStatus.dependencies"
            :key="name"
            class="dependency-item"
          >
            <span class="dep-name">{{ name }}</span>
            <a-tag :color="status ? 'green' : 'red'">
              {{ status ? text.normal : text.abnormal }}
            </a-tag>
          </div>
        </div>
      </div>

      <!-- 向量存储状态 -->
      <div class="status-section">
        <h3>{{ text.vectorStoreStatus }}</h3>
        <div class="vector-info">
          <div class="info-item">
            <span class="label">{{ text.totalKnowledgeBases }}</span>
            <span class="value">{{ systemStatus.vector_stores.total_knowledge_bases }}</span>
          </div>
          <div class="info-item">
            <span class="label">{{ text.activeKnowledgeBases }}</span>
            <span class="value">{{ systemStatus.vector_stores.active_knowledge_bases }}</span>
          </div>
          <div class="info-item">
            <span class="label">{{ text.cacheStatus }}</span>
            <span class="value">{{ systemStatus.vector_stores.cache_status }}</span>
          </div>
        </div>
      </div>

      <!-- 操作按钮 -->
      <div class="actions">
        <a-button @click="fetchSystemStatus" :loading="loading">
          {{ text.refreshStatus }}
        </a-button>
        <a-button type="primary" @click="handleClose">
          {{ text.close }}
        </a-button>
      </div>
    </div>
  </a-modal>
</template>

<script setup lang="ts">
import { ref, watch, computed } from 'vue';
import { Message } from '@arco-design/web-vue';
import { useAppI18n } from '@/composables/useAppI18n';
import { KnowledgeService } from '../services/knowledgeService';
import type { SystemStatusResponse } from '../types/knowledge';

interface Props {
  visible: boolean;
}

const props = defineProps<Props>();
const emit = defineEmits<{
  close: [];
}>();
const { isEnglish } = useAppI18n();

const text = computed(() => (
  isEnglish.value
    ? {
        modalTitle: 'System status check',
        loading: 'Checking system status...',
        retry: 'Retry',
        systemHealthy: 'System healthy',
        systemUnhealthy: 'System unhealthy',
        checkedAt: 'Checked at: ',
        embeddingStatus: 'Embedding model status',
        modelName: 'Model name:',
        status: 'Status:',
        normal: 'Normal',
        abnormal: 'Abnormal',
        modelExists: 'Model exists:',
        yes: 'Yes',
        no: 'No',
        loadTest: 'Load test:',
        passed: 'Passed',
        failed: 'Failed',
        vectorDimension: 'Vector dimension:',
        cachePath: 'Cache path:',
        dependencies: 'Dependencies',
        vectorStoreStatus: 'Vector store status',
        totalKnowledgeBases: 'Total knowledge bases:',
        activeKnowledgeBases: 'Active knowledge bases:',
        cacheStatus: 'Cache status:',
        refreshStatus: 'Refresh status',
        close: 'Close',
        fetchFailed: 'Failed to fetch system status',
        fetchFailedRetry: 'Failed to fetch system status. Please try again later.',
      }
    : {
        modalTitle: '系统状态检查',
        loading: '正在检查系统状态...',
        retry: '重新检查',
        systemHealthy: '系统正常',
        systemUnhealthy: '系统异常',
        checkedAt: '检查时间: ',
        embeddingStatus: '嵌入模型状态',
        modelName: '模型名称:',
        status: '状态:',
        normal: '正常',
        abnormal: '异常',
        modelExists: '模型存在:',
        yes: '是',
        no: '否',
        loadTest: '加载测试:',
        passed: '通过',
        failed: '失败',
        vectorDimension: '向量维度:',
        cachePath: '缓存路径:',
        dependencies: '依赖库状态',
        vectorStoreStatus: '向量存储状态',
        totalKnowledgeBases: '总知识库数:',
        activeKnowledgeBases: '活跃知识库数:',
        cacheStatus: '缓存状态:',
        refreshStatus: '刷新状态',
        close: '关闭',
        fetchFailed: '获取系统状态失败',
        fetchFailedRetry: '获取系统状态失败，请稍后重试',
      }
));

// 响应式数据
const loading = ref(false);
const error = ref<string | null>(null);
const systemStatus = ref<SystemStatusResponse | null>(null);

// 方法
const fetchSystemStatus = async () => {
  loading.value = true;
  error.value = null;
  
  try {
    const status = await KnowledgeService.getSystemStatus();
    systemStatus.value = status;
  } catch (err) {
    console.error('获取系统状态失败:', err);
    error.value = text.value.fetchFailedRetry;
    Message.error(text.value.fetchFailed);
  } finally {
    loading.value = false;
  }
};

const formatTimestamp = (timestamp: number) => {
  return new Date(timestamp * 1000).toLocaleString(isEnglish.value ? 'en-US' : 'zh-CN');
};

const handleClose = () => {
  emit('close');
};

// 监听弹窗显示状态
watch(() => props.visible, (visible) => {
  if (visible) {
    fetchSystemStatus();
  } else {
    // 重置状态
    systemStatus.value = null;
    error.value = null;
  }
});
</script>

<style scoped>
.loading-container {
  text-align: center;
  padding: 40px;
}

.loading-container p {
  margin-top: 16px;
  color: var(--theme-text-secondary);
}

.error-container {
  text-align: center;
}

.retry-container {
  margin-top: 16px;
}

.status-container {
  max-height: 600px;
  overflow-y: auto;
}

.status-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 24px;
  padding-bottom: 16px;
  border-bottom: 1px solid var(--theme-border);
}

.timestamp {
  font-size: 12px;
  color: var(--theme-text-secondary);
}

.status-section {
  margin-bottom: 24px;
}

.status-section h3 {
  margin: 0 0 16px 0;
  font-size: 16px;
  font-weight: bold;
  color: var(--theme-text);
}

.model-info,
.vector-info {
  background: color-mix(in srgb, var(--theme-surface-soft) 72%, white 28%);
  padding: 16px;
  border-radius: 6px;
}

.info-item {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 8px;
}

.info-item:last-child {
  margin-bottom: 0;
}

.label {
  font-weight: 500;
  color: var(--theme-text);
  min-width: 100px;
}

.value {
  color: var(--theme-text-secondary);
  text-align: right;
}

.value.small {
  font-size: 12px;
  word-break: break-all;
}

.dependencies-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
  gap: 12px;
}

.dependency-item {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 8px 12px;
  background: color-mix(in srgb, var(--theme-surface-soft) 72%, white 28%);
  border-radius: 4px;
}

.dep-name {
  font-weight: 500;
  color: var(--theme-text);
}

.actions {
  display: flex;
  justify-content: flex-end;
  gap: 12px;
  margin-top: 24px;
  padding-top: 16px;
  border-top: 1px solid var(--theme-border);
}
</style>
