<template>
  <div class="knowledge-base-detail">
    <div class="detail-header">
      <div class="header-main">
        <h3>{{ knowledgeBase.name }}</h3>
        <div class="header-stats">
          <span class="stat-pill">
            <strong>{{ documents.length || knowledgeBase.document_count }}</strong>
            {{ text.documentCount }}
          </span>
          <span class="stat-pill">
            <strong>{{ knowledgeBase.chunk_count }}</strong>
            {{ text.chunkCount }}
          </span>
          <a-tag size="small" :color="knowledgeBase.is_active ? 'green' : 'red'">
            {{ knowledgeBase.is_active ? text.enabled : text.disabled }}
          </a-tag>
        </div>
      </div>
      <a-button type="text" @click="$emit('close')">
        <template #icon><icon-close /></template>
      </a-button>
    </div>

    <div class="detail-content">
      <div class="meta-bar">
        <span class="meta-item">
          <em>{{ text.project }}</em>{{ getProjectName(knowledgeBase.project) }}
        </span>
        <span class="meta-item">
          <em>{{ text.creator }}</em>{{ knowledgeBase.creator_name || text.unknown }}
        </span>
        <span class="meta-item">
          <em>{{ text.chunkSize }}</em>{{ knowledgeBase.chunk_size }}
        </span>
        <span class="meta-item">
          <em>{{ text.chunkOverlap }}</em>{{ knowledgeBase.chunk_overlap }}
        </span>
        <span v-if="knowledgeBase.description" class="meta-item meta-desc">
          <em>{{ text.description }}</em>{{ knowledgeBase.description }}
        </span>
      </div>

      <!-- 钉钉同步绑定 -->
      <div class="info-section dingtalk-binding">
        <div class="section-header">
          <h4>{{ text.dingtalkSync }}</h4>
          <a-space>
            <a-button size="mini" type="outline" :loading="bindingLoading" @click="loadDingTalkBinding">
              {{ text.refresh }}
            </a-button>
            <a-button size="mini" type="outline" :loading="bindingSaving" @click="saveDingTalkBinding">
              {{ text.saveBinding }}
            </a-button>
            <a-button size="mini" type="primary" :loading="bindingSyncing" @click="runDingTalkSync">
              {{ text.syncNow }}
            </a-button>
          </a-space>
        </div>
        <a-row :gutter="8">
          <a-col :span="8">
            <div class="info-item">
              <span class="label">{{ text.workspaceId }}</span>
              <a-input v-model="bindingForm.workspace_id" size="mini" :placeholder="text.workspaceIdPlaceholder" />
            </div>
          </a-col>
          <a-col :span="8">
            <div class="info-item">
              <span class="label">{{ text.workspaceName }}</span>
              <a-input v-model="bindingForm.workspace_name" size="mini" placeholder="可选" />
            </div>
          </a-col>
          <a-col :span="8">
            <div class="info-item">
              <span class="label">{{ text.rootNodeId }}</span>
              <a-input v-model="bindingForm.root_node_id" size="mini" :placeholder="text.rootNodeIdPlaceholder" />
            </div>
          </a-col>
          <a-col :span="8">
            <div class="info-item inline-row">
              <span class="label">{{ text.intervalMinutes }}</span>
              <a-input-number v-model="bindingForm.interval_minutes" :min="15" :max="10080" size="mini" style="width: 110px" />
              <span class="label tight">{{ text.enableSchedule }}</span>
              <a-switch v-model="bindingForm.enabled" size="small" />
            </div>
          </a-col>
        </a-row>
        <div v-if="bindingForm.last_status" class="binding-status">
          <a-tag size="small" :color="bindingStatusColor">{{ bindingForm.last_status }}</a-tag>
          <span v-if="bindingForm.last_synced_at" class="muted">
            {{ text.lastSyncedAt }}: {{ formatDate(bindingForm.last_synced_at) }}
          </span>
          <span v-if="syncReportText" class="muted">{{ syncReportText }}</span>
          <div v-if="bindingForm.last_error" class="error-text">{{ bindingForm.last_error }}</div>
        </div>
      </div>

      <!-- 文档管理 -->
      <div class="documents-section">
        <div class="section-header">
          <h4>{{ text.documentManagement }}</h4>
          <a-space>
            <a-button type="outline" size="mini" @click="fetchDocuments" :loading="documentsLoading">
              <template #icon><icon-refresh /></template>
              {{ text.refresh }}
            </a-button>
            <a-button type="primary" size="mini" @click="showUploadModal">
              <template #icon><icon-upload /></template>
              {{ text.uploadDocument }}
            </a-button>
          </a-space>
        </div>

        <div class="documents-list">
          <a-table
            :columns="documentColumns"
            :data="documents"
            :loading="documentsLoading"
            :pagination="false"
            size="small"
          >
            <template #title="{ record }">
              <span
                class="document-title-link"
                @click="viewDocument(record.id)"
              >
                {{ record.title }}
              </span>
            </template>

            <template #status="{ record }">
              <div class="status-cell">
                <a-tag size="small" :color="getStatusColor(record.status)">
                  {{ getStatusText(record.status) }}
                </a-tag>
                <a-tooltip v-if="record.status === 'failed' && record.error_message" :content="formatDocumentErrorMessage(record.error_message)">
                  <icon-exclamation-circle style="color: #f53f3f; margin-left: 4px; cursor: help;" />
                </a-tooltip>
              </div>
            </template>

            <template #actions="{ record }">
              <a-space :size="4" wrap class="doc-actions">
                <a-button
                  type="text"
                  size="mini"
                  @click="viewDocument(record.id)"
                >
                  {{ text.view }}
                </a-button>
                <a-button
                  v-if="record.status === 'failed'"
                  type="text"
                  size="mini"
                  @click="reprocessDocument(record.id)"
                >
                  {{ text.retry }}
                </a-button>
                <a-popconfirm
                  :content="text.confirmDeleteDocument"
                  @ok="deleteDocument(record.id)"
                >
                  <a-button type="text" size="mini" status="danger">
                    {{ text.delete }}
                  </a-button>
                </a-popconfirm>
              </a-space>
            </template>
          </a-table>
        </div>
      </div>

      <!-- 查询测试 -->
      <div class="query-section">
        <h4>{{ text.queryTest }}</h4>
        <div class="query-form">
          <div class="query-row">
            <a-textarea
              v-model="queryText"
              :placeholder="text.queryPlaceholder"
              :auto-size="{ minRows: 2, maxRows: 4 }"
              class="query-input"
            />
            <a-button
              type="primary"
              :loading="queryLoading"
              @click="testQuery"
              class="query-submit"
            >
              {{ text.runQueryTest }}
            </a-button>
          </div>

          <div class="query-settings">
            <div class="setting-item">
              <label>{{ text.similarityThreshold }}</label>
              <a-slider
                v-model="similarityThreshold"
                :min="0.1"
                :max="1.0"
                :step="0.1"
                :show-tooltip="true"
                style="width: 120px;"
              />
              <span class="value-display">{{ similarityThreshold }}</span>
            </div>

            <div class="setting-item">
              <label>{{ text.retrievalCount }}</label>
              <a-input-number
                v-model="topK"
                :min="1"
                :max="20"
                :step="1"
                size="mini"
                style="width: 72px;"
              />
            </div>
          </div>
        </div>

        <div v-if="queryResult" class="query-result">
          <h5>{{ text.queryResult }}</h5>
          <div class="result-content">
            <div class="query-info">
              <strong>{{ text.queryLabel }}</strong>
              <p>{{ queryResult.query }}</p>
            </div>
            <div class="answer" v-if="queryResult.answer">
              <strong>{{ text.answerLabel }}</strong>
              <div class="answer-content" v-html="renderAnswer(queryResult)"></div>
            </div>
            <div class="sources">
              <strong>{{ text.relatedContent(queryResult.sources.length) }}</strong>
              <div
                v-for="(source, index) in queryResult.sources"
                :key="index"
                class="source-item"
              >
                <div v-if="source.metadata.content_type === 'image' && source.metadata.image_url" class="source-image">
                  <img :src="source.metadata.image_url" :alt="source.content || text.knowledgeImage" loading="lazy" @click="previewQueryImage(source.metadata.image_url)" />
                  <span class="image-label">{{ source.content || text.image }}</span>
                </div>
                <div v-else-if="source.metadata.resolved_images?.length" class="source-content-with-images">
                  <template v-for="(seg, sIdx) in parseSourceContent(source)" :key="sIdx">
                    <img v-if="seg.type === 'image'" :src="seg.imageUrl" :alt="text.imageN(seg.imageIndex! + 1)" class="source-inline-image" loading="lazy" @click="previewQueryImage(seg.imageUrl!)" />
                    <span v-else>{{ seg.text }}</span>
                  </template>
                </div>
                <div v-else class="source-content">{{ source.content }}</div>
                <div class="source-meta">
                  <span>{{ text.documentN(source.metadata.title) }}</span> |
                  <span>{{ text.similarity((source.similarity_score * 100).toFixed(1)) }}</span>
                  <span v-if="source.metadata.page"> | {{ text.page(source.metadata.page) }}</span>
                </div>
              </div>
            </div>

            <a-modal v-model:visible="queryImagePreviewVisible" :footer="false" :width="800" :title="text.imagePreview">
              <img :src="queryPreviewImageUrl" style="width: 100%;" />
            </a-modal>
            <div class="timing">
              <small>
                {{ text.retrievalTime(queryResult.retrieval_time.toFixed(2)) }} |
                {{ text.generationTime(queryResult.generation_time.toFixed(2)) }} |
                {{ text.totalTime(queryResult.total_time.toFixed(2)) }}
              </small>
            </div>
          </div>
        </div>
      </div>
    </div>

    <!-- 上传文档弹窗 -->
    <DocumentUploadModal
      :visible="isUploadModalVisible"
      :knowledge-base-id="knowledgeBase.id"
      @submit="handleDocumentUploaded"
      @cancel="closeUploadModal"
    />

    <!-- 文档详情弹窗 -->
    <DocumentDetailModal
      :visible="isDocumentDetailVisible"
      :document-id="selectedDocumentId"
      @close="closeDocumentDetail"
    />
  </div>
</template>

<script setup lang="ts">
import { ref, onMounted, computed, reactive, watch } from 'vue';
import { Message } from '@arco-design/web-vue';
import { IconClose, IconUpload, IconExclamationCircle, IconRefresh } from '@arco-design/web-vue/es/icon';
import { useProjectStore } from '@/store/projectStore';
import { KnowledgeService } from '../services/knowledgeService';
import type { KnowledgeBase, Document, QueryResponse, DingTalkSyncBinding } from '../types/knowledge';
import DocumentUploadModal from './DocumentUploadModal.vue';
import DocumentDetailModal from './DocumentDetailModal.vue';
import { useAppI18n } from '@/composables/useAppI18n';
import { translateLegacyText, type AppLocale } from '@/i18n';

interface Props {
  knowledgeBase: KnowledgeBase;
}

const props = defineProps<Props>();
const emit = defineEmits<{
  refresh: [];
  close: [];
}>();
const { isEnglish } = useAppI18n();
const currentLocale = computed<AppLocale>(() => (isEnglish.value ? 'en-US' : 'zh-CN'));

const text = computed(() => (
  isEnglish.value
    ? {
        basicInfo: 'Basic info',
        description: 'Description:',
        noDescription: 'No description',
        project: 'Project:',
        status: 'Status:',
        enabled: 'Enabled',
        disabled: 'Disabled',
        creator: 'Creator:',
        unknown: 'Unknown',
        createdAt: 'Created at:',
        configInfo: 'Config info',
        chunkSize: 'Chunk size:',
        chunkOverlap: 'Chunk overlap:',
        statistics: 'Statistics',
        documentCount: 'Documents',
        chunkCount: 'Chunks',
        documentManagement: 'Document management',
        refresh: 'Refresh',
        uploadDocument: 'Upload document',
        view: 'View',
        retry: 'Retry',
        delete: 'Delete',
        confirmDeleteDocument: 'Are you sure you want to delete this document?',
        queryTest: 'Query test',
        queryPlaceholder: 'Enter query...',
        similarityThreshold: 'Similarity threshold:',
        retrievalCount: 'Top K:',
        runQueryTest: 'Run query',
        queryResult: 'Query result',
        queryLabel: 'Query:',
        answerLabel: 'Answer:',
        relatedContent: (count: number) => `Related content (${count} result(s)):`,
        knowledgeImage: 'Knowledge base image',
        image: 'Image',
        imageN: (index: number) => `Image ${index}`,
        documentN: (title: string) => `Document: ${title}`,
        similarity: (value: string) => `Similarity: ${value}%`,
        page: (value: string | number) => `Page: ${value}`,
        imagePreview: 'Image preview',
        retrievalTime: (seconds: string) => `Retrieval: ${seconds}s`,
        generationTime: (seconds: string) => `Generation: ${seconds}s`,
        totalTime: (seconds: string) => `Total: ${seconds}s`,
        columnTitle: 'Document name',
        columnType: 'Type',
        columnStatus: 'Status',
        columnChunks: 'Chunks',
        columnUploader: 'Uploader',
        columnUploadedAt: 'Uploaded at',
        columnActions: 'Actions',
        statusPending: 'Pending',
        statusProcessing: 'Processing',
        statusCompleted: 'Completed',
        statusFailed: 'Failed',
        fetchDocumentsFailed: 'Failed to fetch documents',
        reprocessStarted: 'Document reprocessing started',
        reprocessFailed: 'Failed to reprocess document',
        deleteSuccess: 'Document deleted',
        deleteFailed: 'Failed to delete document',
        queryRequired: 'Please enter query text',
        queryFailed: 'Query failed',
        uploadSuccess: 'Document uploaded',
        dingtalkSync: 'DingTalk sync',
        saveBinding: 'Save binding',
        syncNow: 'Sync now',
        workspaceId: 'Workspace ID',
        workspaceIdPlaceholder: 'DingTalk workspaceId',
        workspaceName: 'Workspace name',
        rootNodeId: 'Root node ID',
        rootNodeIdPlaceholder: 'Optional; empty = whole workspace',
        intervalMinutes: 'Interval (min)',
        enableSchedule: 'Schedule',
        lastSyncedAt: 'Last sync',
        bindingSaved: 'DingTalk binding saved',
        bindingSaveFailed: 'Failed to save binding',
        syncStarted: 'DingTalk sync started',
        syncFailed: 'Failed to start sync',
      }
    : {
        basicInfo: '基本信息',
        description: '描述:',
        noDescription: '暂无描述',
        project: '所属项目:',
        status: '状态:',
        enabled: '启用',
        disabled: '禁用',
        creator: '创建者:',
        unknown: '未知',
        createdAt: '创建时间:',
        configInfo: '配置信息',
        chunkSize: '分块大小:',
        chunkOverlap: '分块重叠:',
        statistics: '统计信息',
        documentCount: '文档数量',
        chunkCount: '分块数量',
        documentManagement: '文档管理',
        refresh: '刷新',
        uploadDocument: '上传文档',
        view: '查看',
        retry: '重试',
        delete: '删除',
        confirmDeleteDocument: '确定要删除这个文档吗？',
        queryTest: '查询测试',
        queryPlaceholder: '输入查询内容...',
        similarityThreshold: '相似度阈值:',
        retrievalCount: '检索数量:',
        runQueryTest: '测试查询',
        queryResult: '查询结果',
        queryLabel: '查询内容:',
        answerLabel: '回答:',
        relatedContent: (count: number) => `相关内容 (${count} 条结果):`,
        knowledgeImage: '知识库图片',
        image: '图片',
        imageN: (index: number) => `图片 ${index}`,
        documentN: (title: string) => `文档: ${title}`,
        similarity: (value: string) => `相似度: ${value}%`,
        page: (value: string | number) => `页码: ${value}`,
        imagePreview: '图片预览',
        retrievalTime: (seconds: string) => `检索时间: ${seconds}s`,
        generationTime: (seconds: string) => `生成时间: ${seconds}s`,
        totalTime: (seconds: string) => `总时间: ${seconds}s`,
        columnTitle: '文档名称',
        columnType: '类型',
        columnStatus: '状态',
        columnChunks: '分块数',
        columnUploader: '上传者',
        columnUploadedAt: '上传时间',
        columnActions: '操作',
        statusPending: '待处理',
        statusProcessing: '处理中',
        statusCompleted: '已完成',
        statusFailed: '失败',
        fetchDocumentsFailed: '获取文档列表失败',
        reprocessStarted: '文档重新处理已开始',
        reprocessFailed: '重新处理文档失败',
        deleteSuccess: '文档删除成功',
        deleteFailed: '删除文档失败',
        queryRequired: '请输入查询内容',
        queryFailed: '查询失败',
        uploadSuccess: '文档上传成功',
        dingtalkSync: '钉钉同步',
        saveBinding: '保存绑定',
        syncNow: '立即同步',
        workspaceId: '钉钉知识库 ID',
        workspaceIdPlaceholder: 'workspaceId（测试连接可看到）',
        workspaceName: '知识库名称',
        rootNodeId: '根节点 ID',
        rootNodeIdPlaceholder: '可选，空=整库',
        intervalMinutes: '间隔(分钟)',
        enableSchedule: '定时同步',
        lastSyncedAt: '上次同步',
        bindingSaved: '钉钉绑定已保存',
        bindingSaveFailed: '保存绑定失败',
        syncStarted: '钉钉同步已启动',
        syncFailed: '启动同步失败',
      }
));

const projectStore = useProjectStore();

// 响应式数据
const documents = ref<Document[]>([]);
const documentsLoading = ref(false);
const queryText = ref('');
const queryLoading = ref(false);
const queryResult = ref<QueryResponse | null>(null);
const similarityThreshold = ref(0.3);
const topK = ref(3);
const isUploadModalVisible = ref(false);
const isDocumentDetailVisible = ref(false);
const selectedDocumentId = ref<string | null>(null);
const bindingLoading = ref(false);
const bindingSaving = ref(false);
const bindingSyncing = ref(false);
const bindingForm = reactive({
  workspace_id: '',
  workspace_name: '',
  root_node_id: '',
  enabled: true,
  interval_minutes: 60,
  last_status: '',
  last_synced_at: '' as string | null,
  last_error: '',
  last_report: {} as Record<string, unknown>,
});

const bindingStatusColor = computed(() => {
  const map: Record<string, string> = {
    idle: 'gray',
    running: 'blue',
    success: 'green',
    failed: 'red',
  };
  return map[bindingForm.last_status] || 'gray';
});

const syncReportText = computed(() => {
  const r = bindingForm.last_report || {};
  if (!Object.keys(r).length) return '';
  const parts = [
    r.created != null ? `新增 ${r.created}` : '',
    r.updated != null ? `更新 ${r.updated}` : '',
    r.skipped != null ? `跳过 ${r.skipped}` : '',
    r.archived != null ? `归档 ${r.archived}` : '',
    r.failed != null ? `失败 ${r.failed}` : '',
  ].filter(Boolean);
  return parts.join(' · ');
});

const applyBinding = (binding: DingTalkSyncBinding) => {
  bindingForm.workspace_id = binding.workspace_id || '';
  bindingForm.workspace_name = binding.workspace_name || '';
  bindingForm.root_node_id = binding.root_node_id || '';
  bindingForm.enabled = binding.enabled !== false;
  bindingForm.interval_minutes = binding.interval_minutes || 60;
  bindingForm.last_status = binding.last_status || '';
  bindingForm.last_synced_at = binding.last_synced_at || null;
  bindingForm.last_error = binding.last_error || '';
  bindingForm.last_report = (binding.last_report || {}) as Record<string, unknown>;
};

const loadDingTalkBinding = async () => {
  bindingLoading.value = true;
  try {
    const binding = await KnowledgeService.getDingTalkBinding(props.knowledgeBase.id);
    applyBinding(binding);
  } catch (e: any) {
    console.warn(e);
  } finally {
    bindingLoading.value = false;
  }
};

const saveDingTalkBinding = async () => {
  if (!bindingForm.workspace_id.trim()) {
    Message.warning(text.value.workspaceIdPlaceholder);
    return;
  }
  bindingSaving.value = true;
  try {
    const binding = await KnowledgeService.updateDingTalkBinding(props.knowledgeBase.id, {
      workspace_id: bindingForm.workspace_id.trim(),
      workspace_name: bindingForm.workspace_name,
      root_node_id: bindingForm.root_node_id.trim(),
      enabled: bindingForm.enabled,
      interval_minutes: bindingForm.interval_minutes,
    });
    applyBinding(binding);
    Message.success(text.value.bindingSaved);
  } catch (e: any) {
    Message.error(e?.message || text.value.bindingSaveFailed);
  } finally {
    bindingSaving.value = false;
  }
};

const runDingTalkSync = async () => {
  bindingSyncing.value = true;
  try {
    if (!bindingForm.workspace_id.trim()) {
      await saveDingTalkBinding();
    }
    await KnowledgeService.syncDingTalkBinding(props.knowledgeBase.id);
    Message.success(text.value.syncStarted);
    setTimeout(() => {
      loadDingTalkBinding();
      fetchDocuments();
    }, 2000);
  } catch (e: any) {
    Message.error(e?.message || text.value.syncFailed);
  } finally {
    bindingSyncing.value = false;
  }
};
const queryImagePreviewVisible = ref(false);
const queryPreviewImageUrl = ref('');

const previewQueryImage = (url: string) => {
  queryPreviewImageUrl.value = url;
  queryImagePreviewVisible.value = true;
};

/** 将 answer 中的图片引用替换为 <img> 标签 */
const renderAnswer = (result: QueryResponse): string => {
  let html = escapeHtml(result.answer);

  // 从 sources 中收集图片 URL 映射
  const imageUrlByContent = new Map<string, string>();
  const resolvedImageUrls = new Map<number, string>();
  for (const src of result.sources) {
    if (src.metadata.content_type === 'image' && src.metadata.image_url) {
      imageUrlByContent.set(src.content.trim(), src.metadata.image_url);
    }
    // 收集 resolved_images 中的图片
    if (src.metadata.resolved_images) {
      for (const img of src.metadata.resolved_images) {
        resolvedImageUrls.set(img.image_index, img.image_url);
      }
    }
  }

  // 替换 [图片N] 引用
  html = html.replace(/\[图片(\d+)\]\s*/g, (_match, idx) => {
    const key = `[图片${idx}] `;
    const altKey = `[图片${idx}]`;
    const url = imageUrlByContent.get(key) || imageUrlByContent.get(altKey);
    if (url) {
      const alt = isEnglish.value ? `Image ${idx}` : `图片${idx}`;
      return `<img src="${url}" alt="${alt}" class="answer-inline-image" loading="lazy" />`;
    }
    return _match;
  });

  // 替换 {{IMAGE:N}} 占位符
  html = html.replace(/\{\{IMAGE:(\d+)\}\}/g, (_match, idx) => {
    const imgIdx = parseInt(idx, 10);
    const url = resolvedImageUrls.get(imgIdx);
    if (url) {
      const alt = isEnglish.value ? `Image ${imgIdx + 1}` : `图片${imgIdx + 1}`;
      return `<img src="${url}" alt="${alt}" class="answer-inline-image" loading="lazy" />`;
    }
    return '';
  });

  return html;
};

interface ContentSegment {
  type: 'text' | 'image';
  text?: string;
  imageUrl?: string;
  imageIndex?: number;
}

/** 将 source content 中的 {{IMAGE:N}} 解析为文本和图片段 */
const parseSourceContent = (source: { content: string; metadata: Record<string, any> }): ContentSegment[] => {
  const text = source.content;
  const resolvedImages = source.metadata.resolved_images as Array<{ image_index: number; image_url: string }> | undefined;
  if (!resolvedImages?.length) return [{ type: 'text', text }];

  const imageMap = new Map<number, string>();
  for (const img of resolvedImages) {
    imageMap.set(img.image_index, img.image_url);
  }

  const segments: ContentSegment[] = [];
  const pattern = /\{\{IMAGE:(\d+)\}\}/g;
  let lastIndex = 0;
  let match: RegExpExecArray | null;

  while ((match = pattern.exec(text)) !== null) {
    if (match.index > lastIndex) {
      segments.push({ type: 'text', text: text.slice(lastIndex, match.index) });
    }
    const imgIdx = parseInt(match[1], 10);
    const url = imageMap.get(imgIdx);
    if (url) {
      segments.push({ type: 'image', imageUrl: url, imageIndex: imgIdx });
    }
    lastIndex = pattern.lastIndex;
  }

  if (lastIndex < text.length) {
    segments.push({ type: 'text', text: text.slice(lastIndex) });
  }

  return segments;
};

const escapeHtml = (text: string): string => {
  const div = document.createElement('div');
  div.textContent = text;
  return div.innerHTML;
};

// 文档表格列配置
const documentColumns = computed(() => ([
  {
    title: text.value.columnTitle,
    dataIndex: 'title',
    width: 120,
    slotName: 'title',
  },
  {
    title: text.value.columnType,
    dataIndex: 'document_type',
    width: 50,
  },
  {
    title: text.value.columnStatus,
    dataIndex: 'status',
    slotName: 'status',
    width: 70,
  },
  {
    title: text.value.columnChunks,
    dataIndex: 'chunk_count',
    width: 60,
  },
  {
    title: text.value.columnUploader,
    dataIndex: 'uploader_name',
    width: 80,
  },
  {
    title: text.value.columnUploadedAt,
    dataIndex: 'uploaded_at',
    width: 100,
    render: ({ record }: { record: Document }) => {
      return new Date(record.uploaded_at).toLocaleDateString(isEnglish.value ? 'en-US' : 'zh-CN');
    },
  },
  {
    title: text.value.columnActions,
    slotName: 'actions',
    width: isEnglish.value ? 220 : 180,
    fixed: 'right',
  },
]));

// 方法
const formatDate = (dateString: string | null | undefined) => {
  if (!dateString) return '';
  return new Date(dateString).toLocaleString(isEnglish.value ? 'en-US' : 'zh-CN');
};

const formatDocumentErrorMessage = (message?: string) => {
  if (!message) return '';
  return translateLegacyText(message, currentLocale.value);
};

const getStatusColor = (status: string) => {
  const colors: Record<string, string> = {
    pending: 'orange',
    processing: 'blue',
    completed: 'green',
    failed: 'red',
  };
  return colors[status] || 'gray';
};

const getStatusText = (status: string) => {
  const texts: Record<string, string> = {
    pending: text.value.statusPending,
    processing: text.value.statusProcessing,
    completed: text.value.statusCompleted,
    failed: text.value.statusFailed,
  };
  return texts[status] || status;
};

const fetchDocuments = async () => {
  documentsLoading.value = true;
  try {
    console.log('开始获取文档列表，知识库ID:', props.knowledgeBase.id);
    const response = await KnowledgeService.getDocuments({
      knowledge_base: props.knowledgeBase.id,
    });
    console.log('获取到的文档数据:', response);
    console.log('文档数组长度:', response?.length);
    documents.value = response;
    console.log('设置后的documents.value:', documents.value);
  } catch (error: any) {
    console.error('获取文档列表失败:', error);
    // 显示具体的错误消息
    const errorMessage = error?.message || text.value.fetchDocumentsFailed;
    Message.error(errorMessage);
  } finally {
    documentsLoading.value = false;
  }
};

const reprocessDocument = async (documentId: string) => {
  try {
    await KnowledgeService.reprocessDocument(documentId);
    Message.success(text.value.reprocessStarted);
    await fetchDocuments();
  } catch (error) {
    console.error('重新处理文档失败:', error);
    Message.error(text.value.reprocessFailed);
  }
};

const deleteDocument = async (documentId: string) => {
  try {
    await KnowledgeService.deleteDocument(documentId);
    Message.success(text.value.deleteSuccess);
    await fetchDocuments();
    emit('refresh');
  } catch (error) {
    console.error('删除文档失败:', error);
    Message.error(text.value.deleteFailed);
  }
};

const testQuery = async () => {
  if (!queryText.value.trim()) {
    Message.warning(text.value.queryRequired);
    return;
  }

  queryLoading.value = true;
  try {
    const result = await KnowledgeService.queryKnowledgeBase(props.knowledgeBase.id, {
      query: queryText.value,
      knowledge_base_id: props.knowledgeBase.id,
      top_k: topK.value,
      similarity_threshold: similarityThreshold.value,
      include_metadata: true,
    });
    queryResult.value = result;
  } catch (error: any) {
    console.error('查询失败:', error);
    // 显示具体的错误消息
    const errorMessage = error?.message || text.value.queryFailed;
    Message.error(errorMessage);
  } finally {
    queryLoading.value = false;
  }
};

const showUploadModal = () => {
  isUploadModalVisible.value = true;
};

const closeUploadModal = () => {
  isUploadModalVisible.value = false;
};

const handleDocumentUploaded = () => {
  closeUploadModal();
  fetchDocuments();
  emit('refresh');
  Message.success(text.value.uploadSuccess);
};

const viewDocument = (documentId: string) => {
  selectedDocumentId.value = documentId;
  isDocumentDetailVisible.value = true;
};

const closeDocumentDetail = () => {
  isDocumentDetailVisible.value = false;
  selectedDocumentId.value = null;
};

const getProjectName = (projectId: number | string) => {
  // 首先尝试从知识库数据中获取项目名称
  if (props.knowledgeBase.project_name) {
    return props.knowledgeBase.project_name;
  }

  // 如果没有，从项目store中获取
  const numericId = typeof projectId === 'string' ? parseInt(projectId, 10) : projectId;
  const project = projectStore.projectOptions.find(p => p.value === numericId);
  return project ? project.label : String(projectId);
};

// 生命周期
onMounted(() => {
  fetchDocuments();
  loadDingTalkBinding();
});

watch(
  () => props.knowledgeBase.id,
  () => {
    fetchDocuments();
    loadDingTalkBinding();
  },
);
</script>

<style scoped>
.knowledge-base-detail {
  height: 100%;
  display: flex;
  flex-direction: column;
  min-height: 0;
}

.detail-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: 12px;
  padding-bottom: 10px;
  border-bottom: 1px solid var(--theme-border);
  margin-bottom: 12px;
}

.header-main {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 10px 16px;
  min-width: 0;
}

.detail-header h3 {
  margin: 0;
  font-size: 17px;
  font-weight: 700;
}

.header-stats {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 8px;
}

.stat-pill {
  display: inline-flex;
  align-items: baseline;
  gap: 4px;
  padding: 2px 8px;
  border-radius: 999px;
  background: color-mix(in srgb, var(--theme-surface-soft) 80%, white 20%);
  border: 1px solid var(--theme-border);
  font-size: 12px;
  color: var(--theme-text-secondary);
}

.stat-pill strong {
  font-size: 14px;
  color: #00a0e9;
}

.detail-content {
  flex: 1;
  overflow-y: auto;
  min-height: 0;
  display: flex;
  flex-direction: column;
  gap: 12px;
  padding-bottom: 8px;
}

.meta-bar {
  display: flex;
  flex-wrap: wrap;
  gap: 6px 14px;
  padding: 8px 10px;
  background: color-mix(in srgb, var(--theme-surface-soft) 72%, white 28%);
  border: 1px solid var(--theme-border);
  border-radius: 6px;
  font-size: 12px;
  color: var(--theme-text);
}

.meta-item em {
  font-style: normal;
  color: var(--theme-text-secondary);
  margin-right: 4px;
}

.meta-desc {
  flex: 1 1 100%;
}

.info-section {
  margin: 0;
  padding: 10px 12px;
  background: color-mix(in srgb, var(--theme-surface-soft) 72%, white 28%);
  border-radius: 6px;
  border: 1px solid var(--theme-border);
}

.info-section h4,
.section-header h4,
.query-section h4 {
  margin: 0;
  font-size: 13px;
  font-weight: 700;
  color: var(--theme-text);
}

.dingtalk-binding .section-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 8px;
  gap: 8px;
}

.dingtalk-binding .info-item {
  flex-direction: column;
  align-items: stretch;
  gap: 2px;
  margin-bottom: 6px;
}

.dingtalk-binding .info-item.inline-row {
  flex-direction: row;
  align-items: center;
  flex-wrap: wrap;
  gap: 8px;
}

.dingtalk-binding .label {
  width: auto;
  font-size: 12px;
}

.dingtalk-binding .label.tight {
  margin-left: 4px;
}

.binding-status {
  display: flex;
  flex-wrap: wrap;
  gap: 6px 8px;
  align-items: center;
  margin-top: 4px;
  font-size: 12px;
}

.binding-status .muted {
  color: var(--theme-text-secondary);
}

.binding-status .error-text {
  width: 100%;
  color: rgb(var(--red-6));
}

.info-item {
  display: flex;
  margin-bottom: 8px;
  align-items: center;
}

.label {
  width: 90px;
  color: var(--theme-text-secondary);
  font-size: 12px;
  font-weight: 500;
  flex-shrink: 0;
}

.documents-section,
.query-section {
  margin: 0;
}

.section-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 8px;
  gap: 8px;
}

.documents-list {
  max-height: none;
}

.status-cell {
  display: flex;
  align-items: center;
}

.doc-actions {
  white-space: nowrap;
}

.query-section h4 {
  margin-bottom: 8px;
}

.query-row {
  display: flex;
  gap: 8px;
  align-items: stretch;
  margin-bottom: 8px;
}

.query-input {
  flex: 1;
  min-width: 0;
}

.query-submit {
  flex-shrink: 0;
  align-self: stretch;
  min-width: 88px;
}

.query-settings {
  display: flex;
  flex-wrap: wrap;
  gap: 12px 20px;
  margin-bottom: 0;
  padding: 8px 10px;
  background: color-mix(in srgb, var(--theme-surface-soft) 72%, white 28%);
  border-radius: 6px;
  border: 1px solid var(--theme-border);
}

.setting-item {
  display: flex;
  align-items: center;
  gap: 8px;
}

.setting-item label {
  font-size: 12px;
  color: var(--theme-text-secondary);
  white-space: nowrap;
}

.value-display {
  font-size: 12px;
  color: var(--theme-text);
  font-weight: 500;
  min-width: 28px;
}

.query-result {
  margin-top: 10px;
  padding: 10px;
  background: color-mix(in srgb, var(--theme-surface-soft) 72%, white 28%);
  border-radius: 6px;
}

.query-result h5 {
  margin: 0 0 8px 0;
  font-size: 12px;
  font-weight: bold;
}

.query-info {
  margin-bottom: 10px;
}

.query-info p {
  margin: 4px 0 0 0;
  font-size: 12px;
  line-height: 1.5;
}

.answer {
  margin-bottom: 10px;
}

.answer-content {
  margin: 4px 0 0 0;
  font-size: 12px;
  line-height: 1.5;
}

.answer-content :deep(.answer-inline-image) {
  max-width: 300px;
  max-height: 200px;
  border-radius: 4px;
  margin: 4px 0;
  cursor: pointer;
  display: block;
}

.sources {
  margin-bottom: 10px;
}

.source-item {
  margin: 6px 0;
  padding: 8px;
  background: var(--theme-surface);
  border-radius: 4px;
  border-left: 3px solid #00a0e9;
}

.source-content {
  font-size: 12px;
  line-height: 1.4;
  margin-bottom: 4px;
}

.source-image {
  margin-bottom: 4px;
  text-align: center;
}

.source-image img {
  max-width: 100%;
  max-height: 250px;
  border-radius: 4px;
  cursor: pointer;
}

.source-image .image-label {
  display: block;
  font-size: 11px;
  color: #999;
  margin-top: 4px;
}

.source-content-with-images {
  font-size: 12px;
  line-height: 1.4;
  margin-bottom: 4px;
}

.source-inline-image {
  max-width: 100%;
  max-height: 200px;
  border-radius: 4px;
  margin: 4px 0;
  cursor: pointer;
  display: block;
}

.source-meta {
  font-size: 10px;
  color: var(--theme-text-secondary);
}

.timing {
  font-size: 10px;
  color: var(--theme-text-tertiary);
  margin-top: 6px;
}

.document-title-link {
  color: #00a0e9;
  cursor: pointer;
  text-decoration: none;
}

.document-title-link:hover {
  color: #0e42d2;
  text-decoration: underline;
}

@media (max-width: 720px) {
  .query-row {
    flex-direction: column;
  }

  .query-submit {
    width: 100%;
    min-height: 36px;
  }
}
</style>
