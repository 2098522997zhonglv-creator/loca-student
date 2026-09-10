<template>
  <a-modal
    :visible="visible"
    :title="`${text.documentDetailPrefix}${documentContent?.title || ''}`"
    :width="1000"
    :footer="false"
    @cancel="handleClose"
  >
    <div v-if="loading" class="loading-container">
      <a-spin size="large" />
      <div class="loading-text">{{ text.loadingDocumentContent }}</div>
    </div>

    <div v-else-if="documentContent" class="document-detail">
      <!-- 基本信息 -->
      <div class="info-section">
        <h4>{{ text.basicInfo }}</h4>
        <a-descriptions :column="2" bordered>
          <a-descriptions-item :label="text.documentTitle">{{ documentContent.title }}</a-descriptions-item>
          <a-descriptions-item :label="text.documentType">{{ getDocumentTypeText(documentContent.document_type) }}</a-descriptions-item>
          <a-descriptions-item :label="text.status">
            <a-tag :color="getStatusColor(documentContent.status)">
              {{ getStatusText(documentContent.status) }}
            </a-tag>
          </a-descriptions-item>
          <a-descriptions-item :label="text.chunkCount">{{ getChunkCount() }}</a-descriptions-item>
          <a-descriptions-item :label="text.uploader">{{ documentContent.uploader_name }}</a-descriptions-item>
          <a-descriptions-item :label="text.uploadTime">{{ formatDate(documentContent.uploaded_at) }}</a-descriptions-item>
          <a-descriptions-item v-if="documentContent.url" :label="text.originalUrl" :span="2">
            <a :href="documentContent.url" target="_blank" rel="noopener noreferrer" class="url-link">
              {{ documentContent.url }}
            </a>
          </a-descriptions-item>
          <a-descriptions-item v-if="documentContent.processed_at" :label="text.processedTime">
            {{ formatDate(documentContent.processed_at) }}
          </a-descriptions-item>
          <a-descriptions-item v-if="documentContent.file_size" :label="text.fileSize">
            {{ formatFileSize(documentContent.file_size) }}
          </a-descriptions-item>
          <a-descriptions-item v-if="documentContent.page_count" :label="text.pageCount">
            {{ documentContent.page_count }}
          </a-descriptions-item>
          <a-descriptions-item v-if="documentContent.word_count" :label="text.wordCount">
            {{ documentContent.word_count }}
          </a-descriptions-item>
          <a-descriptions-item :label="text.knowledgeBase">{{ documentContent.knowledge_base.name }}</a-descriptions-item>
          <a-descriptions-item v-if="documentContent.file_name" :label="text.fileName">
            {{ documentContent.file_name }}
          </a-descriptions-item>
        </a-descriptions>
      </div>

      <!-- 图片预览弹窗 -->
      <a-modal
        :visible="imagePreviewVisible"
        :footer="false"
        :width="900"
        class="image-preview-modal"
        @cancel="imagePreviewVisible = false"
      >
        <img :src="previewImageUrl" class="full-preview-image" />
      </a-modal>

      <!-- 文档内容区域 -->
      <div v-if="showChunks" class="chunks-section">
        <div class="section-header">
          <h4>{{ text.documentContent }}</h4>
          <div class="content-actions">
            <a-switch
              v-model="includeChunks"
              :checked-text="text.chunkView"
              :unchecked-text="text.originalContent"
              @change="handleChunksToggle"
            />
            <a-button
              v-if="documentContent.file_url"
              type="outline"
              size="small"
              @click="downloadFile"
            >
              {{ text.downloadOriginalFile }}
            </a-button>
            <a-button
              v-if="documentContent.url"
              type="primary"
              size="small"
              @click="openOriginalUrl"
            >
              {{ text.viewOriginalWebpage }}
            </a-button>
          </div>
        </div>

        <!-- 分块视图 -->
        <div v-if="includeChunks && documentContent.chunks" class="chunks-content">
          <div class="chunks-info">
            <span class="chunks-summary">
              {{ text.chunkSummary(documentContent.chunks.total_count, chunkPagination.current) }}
            </span>
          </div>

          <div class="chunks-pagination">
            <a-pagination
              :current="chunkPagination.current"
              :page-size="chunkPagination.pageSize"
              :total="documentContent.chunks.total_count"
              :show-total="true"
              :show-jumper="true"
              :show-page-size="true"
              :page-size-options="['5', '10', '20', '50']"
              @change="handleChunkPageChange"
              @page-size-change="handleChunkPageSizeChange"
            />
          </div>

          <div class="chunks-list">
            <div
              v-for="chunk in documentContent.chunks.items"
              :key="chunk.id"
              class="chunk-item"
            >
              <div class="chunk-header">
                <span class="chunk-index">{{ text.chunkIndex(chunk.chunk_index + 1) }}</span>
                <span class="chunk-length">{{ text.characters(chunk.content.length) }}</span>
                <span v-if="chunk.start_index !== null && chunk.end_index !== null" class="chunk-range">
                  {{ text.range(chunk.start_index, chunk.end_index) }}
                </span>
                <span v-if="chunk.page_number" class="chunk-page">
                  {{ text.pageN(chunk.page_number) }}
                </span>
              </div>
              <div class="chunk-content">
                <template v-for="(seg, sIdx) in parseChunkContent(chunk.content)" :key="sIdx">
                  <div v-if="seg.type === 'image'" class="inline-image-block" @click="previewImage(seg.imageUrl!)">
                    <img :src="seg.imageUrl" :alt="text.documentImage(seg.imageIndex! + 1)" loading="lazy" />
                    <span class="inline-image-label">{{ text.figure(seg.imageIndex! + 1) }}</span>
                  </div>
                  <pre v-else>{{ seg.text }}</pre>
                </template>
              </div>
            </div>
          </div>
        </div>

        <!-- 原始内容视图（图文混排） -->
        <div v-else class="original-content-preview">
          <div class="preview-notice">
            <p>{{ text.chunkViewOffNotice }}</p>
          </div>
          <div class="content-display">
            <div class="content-preview">
              <template v-for="(segment, idx) in contentSegments" :key="idx">
                <div v-if="segment.type === 'image'" class="inline-image-block" @click="previewImage(segment.imageUrl!)">
                  <img :src="segment.imageUrl" :alt="text.documentImage(segment.imageIndex! + 1)" loading="lazy" />
                  <span class="inline-image-label">{{ text.figure(segment.imageIndex! + 1) }}</span>
                </div>
                <pre v-else class="content-text">{{ segment.text }}</pre>
              </template>
            </div>
          </div>
        </div>
      </div>
    </div>

    <a-empty v-else :description="text.unableToLoadDocumentContent" />
  </a-modal>
</template>

<script setup lang="ts">
import { ref, watch, computed } from 'vue';
import { Message } from '@arco-design/web-vue';
import { KnowledgeService } from '../services/knowledgeService';
import type { DocumentContentResponse } from '../types/knowledge';
import { useAppI18n } from '@/composables/useAppI18n';

interface Props {
  visible: boolean;
  documentId: string | null;
}

const props = defineProps<Props>();
const emit = defineEmits<{
  close: [];
}>();
const { isEnglish } = useAppI18n();

const text = computed(() => (
  isEnglish.value
    ? {
        documentDetailPrefix: 'Document details - ',
        loadingDocumentContent: 'Loading document content...',
        basicInfo: 'Basic info',
        documentTitle: 'Document title',
        documentType: 'Document type',
        status: 'Status',
        chunkCount: 'Chunk count',
        uploader: 'Uploader',
        uploadTime: 'Upload time',
        originalUrl: 'Original URL',
        processedTime: 'Processed time',
        fileSize: 'File size',
        pageCount: 'Pages',
        wordCount: 'Words',
        knowledgeBase: 'Knowledge base',
        fileName: 'File name',
        documentContent: 'Document content',
        chunkView: 'Chunk view',
        originalContent: 'Original content',
        downloadOriginalFile: 'Download original file',
        viewOriginalWebpage: 'Open original webpage',
        chunkSummary: (total: number, current: number) => `Total ${total} chunks, currently showing page ${current}`,
        chunkIndex: (index: number) => `Chunk #${index}`,
        characters: (count: number) => `${count} chars`,
        range: (start: number, end: number) => `Range: ${start} - ${end}`,
        pageN: (page: number) => `Page ${page}`,
        documentImage: (index: number) => `Document image ${index}`,
        figure: (index: number) => `Fig. ${index}`,
        chunkViewOffNotice: 'Chunk view is off. The original document content preview is shown below:',
        unableToLoadDocumentContent: 'Unable to load document content',
        statusPending: 'Pending',
        statusProcessing: 'Processing',
        statusCompleted: 'Completed',
        statusFailed: 'Failed',
        typePdf: 'PDF document',
        typeDocx: 'Word document',
        typePptx: 'PowerPoint document',
        typeTxt: 'Text file',
        typeMd: 'Markdown document',
        typeHtml: 'HTML document',
        typeUrl: 'Web link',
        fetchDocumentContentFailed: 'Failed to fetch document content',
      }
    : {
        documentDetailPrefix: '文档详情 - ',
        loadingDocumentContent: '正在加载文档内容...',
        basicInfo: '基本信息',
        documentTitle: '文档标题',
        documentType: '文档类型',
        status: '状态',
        chunkCount: '分块数量',
        uploader: '上传者',
        uploadTime: '上传时间',
        originalUrl: '原始URL',
        processedTime: '处理时间',
        fileSize: '文件大小',
        pageCount: '页数',
        wordCount: '字数',
        knowledgeBase: '所属知识库',
        fileName: '文件名',
        documentContent: '文档内容',
        chunkView: '分块视图',
        originalContent: '原始内容',
        downloadOriginalFile: '下载原文件',
        viewOriginalWebpage: '查看原网页',
        chunkSummary: (total: number, current: number) => `共 ${total} 个分块，当前显示第 ${current} 页`,
        chunkIndex: (index: number) => `分块 #${index}`,
        characters: (count: number) => `${count} 字符`,
        range: (start: number, end: number) => `位置: ${start} - ${end}`,
        pageN: (page: number) => `第 ${page} 页`,
        documentImage: (index: number) => `文档图片 ${index}`,
        figure: (index: number) => `图 ${index}`,
        chunkViewOffNotice: '分块显示已关闭，以下是原始文档内容预览：',
        unableToLoadDocumentContent: '无法加载文档内容',
        statusPending: '待处理',
        statusProcessing: '处理中',
        statusCompleted: '已完成',
        statusFailed: '处理失败',
        typePdf: 'PDF文档',
        typeDocx: 'Word文档',
        typePptx: 'PowerPoint文档',
        typeTxt: '文本文件',
        typeMd: 'Markdown文档',
        typeHtml: 'HTML文档',
        typeUrl: '网页链接',
        fetchDocumentContentFailed: '获取文档内容失败',
      }
));

// 响应式数据
const loading = ref(false);
const documentContent = ref<DocumentContentResponse | null>(null);
const includeChunks = ref(false);
const chunkPagination = ref({
  current: 1,
  pageSize: 10,
});
const imagePreviewVisible = ref(false);
const previewImageUrl = ref('');

// 计算属性
const showChunks = computed(() => {
  if (!documentContent.value) return false;
  const chunkCount = documentContent.value.chunks?.total_count ?? documentContent.value.chunk_count ?? 0;
  return chunkCount > 0;
});

// 将文档内容按 {{IMAGE:N}} 占位符分割为文本段和图片段
interface ContentSegment {
  type: 'text' | 'image';
  text?: string;
  imageUrl?: string;
  imageIndex?: number;
}

const contentSegments = computed<ContentSegment[]>(() => {
  return parseImageMarkers(documentContent.value?.content);
});

// 解析文本中的 {{IMAGE:N}} 标记为文本段和图片段
const parseImageMarkers = (text?: string | null): ContentSegment[] => {
  if (!text) return [];

  const images = documentContent.value?.images;
  const imageMap = new Map<number, string>();
  if (images) {
    for (const img of images) {
      imageMap.set(img.image_index, img.image_url);
    }
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

// 用于分块视图中解析内容
const parseChunkContent = (content: string): ContentSegment[] => {
  return parseImageMarkers(content);
};

// 方法
const formatDate = (dateString: string) => {
  return new Date(dateString).toLocaleString(isEnglish.value ? 'en-US' : 'zh-CN');
};

const formatFileSize = (bytes: number) => {
  if (bytes === 0) return '0 B';
  const k = 1024;
  const sizes = ['B', 'KB', 'MB', 'GB'];
  const i = Math.floor(Math.log(bytes) / Math.log(k));
  return parseFloat((bytes / Math.pow(k, i)).toFixed(2)) + ' ' + sizes[i];
};

const getStatusColor = (status: string) => {
  const colorMap: Record<string, string> = {
    pending: 'orange',
    processing: 'blue',
    completed: 'green',
    failed: 'red',
  };
  return colorMap[status] || 'gray';
};

const getStatusText = (status: string) => {
  const textMap: Record<string, string> = {
    pending: text.value.statusPending,
    processing: text.value.statusProcessing,
    completed: text.value.statusCompleted,
    failed: text.value.statusFailed,
  };
  return textMap[status] || status;
};

const getDocumentTypeText = (type: string) => {
  const typeMap: Record<string, string> = {
    pdf: text.value.typePdf,
    docx: text.value.typeDocx,
    pptx: text.value.typePptx,
    txt: text.value.typeTxt,
    md: text.value.typeMd,
    html: text.value.typeHtml,
    url: text.value.typeUrl,
  };
  return typeMap[type] || type.toUpperCase();
};

const getChunkCount = () => {
  if (!documentContent.value) return 0;
  // 优先使用 chunks.total_count，如果没有则使用 chunk_count
  return documentContent.value.chunks?.total_count ?? documentContent.value.chunk_count ?? 0;
};

const fetchDocumentContent = async (documentId: string) => {
  loading.value = true;
  try {
    const params = {
      include_chunks: includeChunks.value,
      chunk_page: chunkPagination.value.current,
      chunk_page_size: chunkPagination.value.pageSize,
    };

    const response = await KnowledgeService.getDocumentContent(documentId, params);
    documentContent.value = response;
  } catch (error) {
    console.error('获取文档内容失败:', error);
    Message.error(text.value.fetchDocumentContentFailed);
    documentContent.value = null;
  } finally {
    loading.value = false;
  }
};

const handleChunksToggle = () => {
  if (props.documentId) {
    chunkPagination.value.current = 1; // 重置分页
    fetchDocumentContent(props.documentId);
  }
};

const handleChunkPageChange = (page: number) => {
  chunkPagination.value.current = page;
  if (props.documentId) {
    fetchDocumentContent(props.documentId);
  }
};

const handleChunkPageSizeChange = (pageSize: number) => {
  chunkPagination.value.pageSize = pageSize;
  chunkPagination.value.current = 1; // 重置到第一页
  if (props.documentId) {
    fetchDocumentContent(props.documentId);
  }
};

const downloadFile = () => {
  if (documentContent.value?.file_url) {
    window.open(documentContent.value.file_url, '_blank');
  }
};

const previewImage = (url: string) => {
  previewImageUrl.value = url;
  imagePreviewVisible.value = true;
};

const openOriginalUrl = () => {
  if (documentContent.value?.url) {
    window.open(documentContent.value.url, '_blank', 'noopener,noreferrer');
  }
};

const handleClose = () => {
  emit('close');
};

// 监听器
watch(
  () => props.visible,
  (newVisible) => {
    if (newVisible && props.documentId) {
      fetchDocumentContent(props.documentId);
    } else if (!newVisible) {
      // 关闭时重置数据
      documentContent.value = null;
      includeChunks.value = true;
      chunkPagination.value = { current: 1, pageSize: 10 };
      imagePreviewVisible.value = false;
      previewImageUrl.value = '';
    }
  }
);

watch(
  () => props.documentId,
  (newDocumentId) => {
    if (props.visible && newDocumentId) {
      fetchDocumentContent(newDocumentId);
    }
  }
);
</script>

<style scoped>
.loading-container {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  padding: 40px;
}

.loading-text {
  margin-top: 12px;
  color: #86909c;
}

.document-detail {
  max-height: 70vh;
  overflow-y: auto;
}

.info-section,
.content-section,
.chunks-section {
  margin-bottom: 24px;
}

.section-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 12px;
}

.section-header h4 {
  margin: 0;
  font-size: 16px;
  font-weight: 600;
}

.content-actions {
  display: flex;
  gap: 8px;
  align-items: center;
}

.url-link {
  color: #165dff;
  text-decoration: none;
  word-break: break-all;
}

.url-link:hover {
  text-decoration: underline;
}

.content-display {
  border: 1px solid var(--theme-border);
  border-radius: 6px;
  overflow: hidden;
}

.content-preview {
  max-height: 400px;
  overflow-y: auto;
  padding: 16px;
  background-color: var(--theme-surface-soft);
}

.content-text {
  margin: 0;
  padding: 0;
  background: none;
  border: none;
  font-family: 'Monaco', 'Menlo', 'Ubuntu Mono', monospace;
  font-size: 13px;
  line-height: 1.6;
  white-space: pre-wrap;
  word-break: break-word;
  overflow-wrap: break-word;
  color: var(--theme-text-primary);
}

.original-content-preview {
  margin-top: 16px;
}

.preview-notice {
  margin-bottom: 12px;
  padding: 8px 12px;
  background-color: color-mix(in srgb, var(--theme-surface) 90%, rgba(var(--theme-accent-rgb), 0.08));
  border: 1px solid rgba(var(--theme-accent-rgb), 0.18);
  border-radius: 4px;
  color: #d46b08;
}

.preview-notice p {
  margin: 0;
  font-size: 14px;
}
.chunks-info {
  margin-bottom: 12px;
  padding: 8px 12px;
  background-color: color-mix(in srgb, var(--theme-surface-soft) 72%, white 28%);
  border-radius: 4px;
  border-left: 3px solid #165dff;
}

.chunks-summary {
  font-size: 14px;
  color: #4e5969;
  font-weight: 500;
}

.chunks-pagination {
  margin-bottom: 16px;
  text-align: right;
}

.chunks-list {
  space-y: 12px;
}

.chunk-item {
  border: 1px solid var(--theme-border);
  border-radius: 6px;
  padding: 16px;
  margin-bottom: 12px;
  background-color: var(--theme-surface);
  transition: box-shadow 0.2s ease;
}

.chunk-item:hover {
  box-shadow: 0 2px 8px rgba(0, 0, 0, 0.1);
}

.chunk-header {
  display: flex;
  gap: 16px;
  margin-bottom: 12px;
  font-size: 12px;
  color: #86909c;
  flex-wrap: wrap;
}

.chunk-index {
  font-weight: 600;
  color: #1d2129;
  background-color: #f2f3f5;
  padding: 2px 8px;
  border-radius: 12px;
  font-size: 11px;
}

.chunk-length {
  color: #165dff;
  font-weight: 500;
}

.chunk-content {
  line-height: 1.6;
}

.chunk-content pre {
  margin: 0;
  padding: 0;
  background: none;
  border: none;
  font-family: inherit;
  font-size: inherit;
  white-space: pre-wrap;
  word-break: break-word;
  overflow-wrap: break-word;
}

/* 图片预览 */
.full-preview-image {
  width: 100%;
  max-height: 80vh;
  object-fit: contain;
}

/* 图文混排内联图片 */
.inline-image-block {
  display: flex;
  flex-direction: column;
  align-items: center;
  margin: 12px 0;
  padding: 8px;
  background: #f7f8fa;
  border: 1px solid #e5e6eb;
  border-radius: 6px;
  cursor: pointer;
  transition: box-shadow 0.2s;
}

.inline-image-block:hover {
  box-shadow: 0 2px 8px rgba(0, 0, 0, 0.12);
}

.inline-image-block img {
  max-width: 100%;
  max-height: 500px;
  object-fit: contain;
}

.inline-image-label {
  margin-top: 4px;
  font-size: 12px;
  color: #86909c;
}
</style>
