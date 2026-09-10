<template>
  <a-modal
    :visible="visible"
    :title="text.modalTitle"
    :width="800"
    :footer="false"
    @cancel="$emit('close')"
  >
    <div v-if="loading" class="loading-container">
      <a-spin size="large" />
      <div class="loading-text">{{ text.loading }}</div>
    </div>

    <div v-else-if="statistics" class="stats-container">
      <!-- 概览统计 -->
      <div class="stats-overview">
        <h3>{{ text.overview }}</h3>
        <div class="stats-grid">
          <div class="stat-card">
            <div class="stat-icon document-icon">
              <icon-file />
            </div>
            <div class="stat-content">
              <div class="stat-value">{{ statistics.document_count }}</div>
              <div class="stat-label">{{ text.totalDocuments }}</div>
            </div>
          </div>

          <div class="stat-card">
            <div class="stat-icon chunk-icon">
              <icon-layers />
            </div>
            <div class="stat-content">
              <div class="stat-value">{{ statistics.chunk_count }}</div>
              <div class="stat-label">{{ text.totalChunks }}</div>
            </div>
          </div>

          <div class="stat-card">
            <div class="stat-icon query-icon">
              <icon-search />
            </div>
            <div class="stat-content">
              <div class="stat-value">{{ statistics.query_count }}</div>
              <div class="stat-label">{{ text.queryCount }}</div>
            </div>
          </div>
        </div>
      </div>

      <!-- 文档状态分布 -->
      <div class="status-distribution">
        <h3>{{ text.documentStatusDistribution }}</h3>
        <div class="status-grid">
          <div class="status-item completed">
            <div class="status-count">{{ statistics.document_status_distribution.completed }}</div>
            <div class="status-label">{{ text.completed }}</div>
          </div>
          <div class="status-item processing">
            <div class="status-count">{{ statistics.document_status_distribution.processing }}</div>
            <div class="status-label">{{ text.processing }}</div>
          </div>
          <div class="status-item failed">
            <div class="status-count">{{ statistics.document_status_distribution.failed }}</div>
            <div class="status-label">{{ text.failed }}</div>
          </div>
        </div>
      </div>

      <!-- 最近查询 -->
      <div class="recent-queries">
        <h3>{{ text.recentQueries }}</h3>
        <div v-if="statistics.recent_queries.length === 0" class="empty-queries">
          {{ text.noRecentQueries }}
        </div>
        <div v-else class="queries-list">
          <div
            v-for="(query, index) in statistics.recent_queries"
            :key="index"
            class="query-item"
          >
            <div class="query-content">
              <div class="query-text">{{ query.query }}</div>
              <div class="query-meta">
                <span class="query-time">{{ formatDate(query.created_at) }}</span>
                <span class="query-duration">{{ text.duration(query.total_time.toFixed(3)) }}</span>
              </div>
            </div>
          </div>
        </div>
      </div>

      <!-- 性能指标 -->
      <div class="performance-metrics">
        <h3>{{ text.performanceMetrics }}</h3>
        <div class="metrics-grid">
          <div class="metric-item">
            <div class="metric-label">{{ text.avgQueryTime }}</div>
            <div class="metric-value">
              {{ getAverageQueryTime() }}s
            </div>
          </div>
          <div class="metric-item">
            <div class="metric-label">{{ text.avgDocumentSize }}</div>
            <div class="metric-value">
              {{ text.chunksPerDocument(getAverageChunksPerDocument()) }}
            </div>
          </div>
        </div>
      </div>
    </div>

    <div v-else class="error-container">
      <a-result status="error" :title="text.loadFailedTitle">
        <template #subtitle>
          {{ text.loadFailedSubtitle }}
        </template>
        <template #extra>
          <a-button type="primary" @click="loadStatistics">
            {{ text.reload }}
          </a-button>
        </template>
      </a-result>
    </div>
  </a-modal>
</template>

<script setup lang="ts">
import { ref, watch, computed } from 'vue';
import { Message } from '@arco-design/web-vue';
import { IconFile, IconLayers, IconSearch } from '@arco-design/web-vue/es/icon';
import { KnowledgeService } from '../services/knowledgeService';
import type { KnowledgeBaseStatistics } from '../types/knowledge';
import { useAppI18n } from '@/composables/useAppI18n';

interface Props {
  visible: boolean;
  knowledgeBaseId: string;
}

const props = defineProps<Props>();
defineEmits<{
  close: [];
}>();
const { isEnglish } = useAppI18n();

const text = computed(() => (
  isEnglish.value
    ? {
        modalTitle: 'Knowledge base statistics',
        loading: 'Loading statistics...',
        overview: 'Overview',
        totalDocuments: 'Total documents',
        totalChunks: 'Total chunks',
        queryCount: 'Queries',
        documentStatusDistribution: 'Document status distribution',
        completed: 'Completed',
        processing: 'Processing',
        failed: 'Failed',
        recentQueries: 'Recent queries',
        noRecentQueries: 'No recent queries',
        duration: (seconds: string) => `Duration: ${seconds}s`,
        performanceMetrics: 'Performance metrics',
        avgQueryTime: 'Average query time',
        avgDocumentSize: 'Average document size',
        chunksPerDocument: (count: string) => `${count} chunks/document`,
        loadFailedTitle: 'Load failed',
        loadFailedSubtitle: 'Unable to load statistics. Please try again later.',
        reload: 'Reload',
        loadStatsFailed: 'Failed to load statistics',
        justNow: 'Just now',
        minutesAgo: (value: number) => `${value} minute(s) ago`,
        hoursAgo: (value: number) => `${value} hour(s) ago`,
        daysAgo: (value: number) => `${value} day(s) ago`,
      }
    : {
        modalTitle: '知识库统计信息',
        loading: '正在加载统计信息...',
        overview: '概览统计',
        totalDocuments: '文档总数',
        totalChunks: '分块总数',
        queryCount: '查询次数',
        documentStatusDistribution: '文档状态分布',
        completed: '已完成',
        processing: '处理中',
        failed: '失败',
        recentQueries: '最近查询',
        noRecentQueries: '暂无查询记录',
        duration: (seconds: string) => `耗时: ${seconds}s`,
        performanceMetrics: '性能指标',
        avgQueryTime: '平均查询时间',
        avgDocumentSize: '平均文档大小',
        chunksPerDocument: (count: string) => `${count} 分块/文档`,
        loadFailedTitle: '加载失败',
        loadFailedSubtitle: '无法加载统计信息，请稍后重试',
        reload: '重新加载',
        loadStatsFailed: '加载统计信息失败',
        justNow: '刚刚',
        minutesAgo: (value: number) => `${value}分钟前`,
        hoursAgo: (value: number) => `${value}小时前`,
        daysAgo: (value: number) => `${value}天前`,
      }
));

const loading = ref(false);
const statistics = ref<KnowledgeBaseStatistics | null>(null);

// 监听弹窗显示状态
watch(() => props.visible, (visible) => {
  if (visible && props.knowledgeBaseId) {
    loadStatistics();
  }
});

// 方法
const loadStatistics = async () => {
  if (!props.knowledgeBaseId) return;

  loading.value = true;
  try {
    const data = await KnowledgeService.getKnowledgeBaseStatistics(props.knowledgeBaseId);
    statistics.value = data;
  } catch (error: any) {
    console.error('加载统计信息失败:', error);
    // 显示具体的错误消息
    const errorMessage = error?.message || text.value.loadStatsFailed;
    Message.error(errorMessage);
    statistics.value = null;
  } finally {
    loading.value = false;
  }
};

const formatDate = (dateString: string) => {
  const date = new Date(dateString);
  const now = new Date();
  const diffMs = now.getTime() - date.getTime();
  const diffMins = Math.floor(diffMs / (1000 * 60));
  const diffHours = Math.floor(diffMs / (1000 * 60 * 60));
  const diffDays = Math.floor(diffMs / (1000 * 60 * 60 * 24));

  if (diffMins < 1) return text.value.justNow;
  if (diffMins < 60) return text.value.minutesAgo(diffMins);
  if (diffHours < 24) return text.value.hoursAgo(diffHours);
  if (diffDays < 7) return text.value.daysAgo(diffDays);
  
  return date.toLocaleDateString();
};

const getAverageQueryTime = () => {
  if (!statistics.value || statistics.value.recent_queries.length === 0) {
    return '0.000';
  }
  
  const totalTime = statistics.value.recent_queries.reduce(
    (sum, query) => sum + query.total_time, 
    0
  );
  const average = totalTime / statistics.value.recent_queries.length;
  return average.toFixed(3);
};

const getAverageChunksPerDocument = () => {
  if (!statistics.value || statistics.value.document_count === 0) {
    return '0';
  }
  
  const average = statistics.value.chunk_count / statistics.value.document_count;
  return average.toFixed(1);
};
</script>

<style scoped>
.loading-container {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  padding: 60px 20px;
}

.loading-text {
  margin-top: 16px;
  color: var(--theme-text-secondary);
  font-size: 14px;
}

.stats-container {
  padding: 20px 0;
}

.stats-container h3 {
  margin: 0 0 16px 0;
  font-size: 16px;
  font-weight: bold;
  color: var(--theme-text);
  border-bottom: 1px solid var(--theme-border);
  padding-bottom: 8px;
}

.stats-overview {
  margin-bottom: 32px;
}

.stats-grid {
  display: grid;
  grid-template-columns: repeat(3, 1fr);
  gap: 16px;
}

.stat-card {
  display: flex;
  align-items: center;
  padding: 20px;
  background: color-mix(in srgb, var(--theme-surface-soft) 72%, white 28%);
  border-radius: 8px;
  border-left: 4px solid #00a0e9;
}

.stat-icon {
  width: 48px;
  height: 48px;
  border-radius: 50%;
  display: flex;
  align-items: center;
  justify-content: center;
  margin-right: 16px;
  font-size: 20px;
  color: white;
}

.document-icon {
  background: #00a0e9;
}

.chunk-icon {
  background: #00b42a;
}

.query-icon {
  background: #ff7d00;
}

.stat-content {
  flex: 1;
}

.stat-value {
  font-size: 24px;
  font-weight: bold;
  color: var(--theme-text);
  line-height: 1;
}

.stat-label {
  font-size: 12px;
  color: var(--theme-text-secondary);
  margin-top: 4px;
}

.status-distribution {
  margin-bottom: 32px;
}

.status-grid {
  display: grid;
  grid-template-columns: repeat(3, 1fr);
  gap: 16px;
}

.status-item {
  text-align: center;
  padding: 16px;
  border-radius: 8px;
  border: 2px solid;
}

.status-item.completed {
  border-color: #00b42a;
  background: rgba(0, 180, 42, 0.1);
}

.status-item.processing {
  border-color: #00a0e9;
  background: rgba(0, 160, 233, 0.1);
}

.status-item.failed {
  border-color: #f53f3f;
  background: rgba(245, 63, 63, 0.1);
}

.status-count {
  font-size: 20px;
  font-weight: bold;
  margin-bottom: 4px;
}

.status-label {
  font-size: 12px;
  color: var(--theme-text-secondary);
}

.recent-queries {
  margin-bottom: 32px;
}

.empty-queries {
  text-align: center;
  color: var(--theme-empty-text);
  padding: 40px 20px;
  background: color-mix(in srgb, var(--theme-surface-soft) 72%, white 28%);
  border-radius: 8px;
}

.queries-list {
  max-height: 200px;
  overflow-y: auto;
}

.query-item {
  padding: 12px 16px;
  border: 1px solid var(--theme-border);
  border-radius: 6px;
  margin-bottom: 8px;
  background: var(--theme-surface);
}

.query-item:last-child {
  margin-bottom: 0;
}

.query-text {
  font-size: 14px;
  color: var(--theme-text);
  margin-bottom: 4px;
  line-height: 1.4;
}

.query-meta {
  display: flex;
  justify-content: space-between;
  font-size: 12px;
  color: var(--theme-text-secondary);
}

.performance-metrics h3 {
  margin-bottom: 16px;
}

.metrics-grid {
  display: grid;
  grid-template-columns: repeat(2, 1fr);
  gap: 16px;
}

.metric-item {
  padding: 16px;
  background: color-mix(in srgb, var(--theme-surface-soft) 72%, white 28%);
  border-radius: 8px;
  text-align: center;
}

.metric-label {
  font-size: 12px;
  color: var(--theme-text-secondary);
  margin-bottom: 8px;
}

.metric-value {
  font-size: 18px;
  font-weight: bold;
  color: #00a0e9;
}

.error-container {
  padding: 20px 0;
}
</style>
