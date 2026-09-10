<template>
  <div class="chat-input-container">
    <div v-if="quotedMessage" class="quote-preview-wrapper">
      <div class="quote-preview">
        <icon-reply class="quote-icon" />
        <span class="quote-text">{{ truncateQuote(quotedMessage.content) }}</span>
        <a-button type="text" size="mini" class="quote-close-btn" @click="$emit('clear-quote')">
          <template #icon><icon-close /></template>
        </a-button>
      </div>
    </div>

      <div v-if="imagePreviews.length > 0" class="image-preview-wrapper">
        <div class="image-preview-header">
          <span class="image-preview-count">{{ text.selectedImages(imagePreviews.length) }}</span>
          <a-button type="text" size="mini" class="clear-images-btn" @click="clearImages">
            {{ text.clear }}
          </a-button>
        </div>
      <div class="image-preview-list">
        <div
          v-for="(preview, index) in imagePreviews"
          :key="`${preview}-${index}`"
          class="image-preview"
        >
          <img :src="preview" :alt="text.previewImageAlt(index + 1)" />
          <span class="image-order-badge">{{ index + 1 }}</span>
          <a-button
            class="remove-image-btn"
            type="text"
            size="small"
            @click="removeImage(index)"
          >
            <template #icon><icon-close /></template>
          </a-button>
        </div>
      </div>
    </div>

    <div v-if="attachmentFiles.length > 0" class="attachment-preview-wrapper">
      <div class="attachment-preview-header">
        <span class="attachment-preview-count">{{ text.selectedAttachments(attachmentFiles.length) }}</span>
        <a-button type="text" size="mini" class="clear-attachments-btn" @click="clearAttachments">
          {{ text.clear }}
        </a-button>
      </div>
      <div class="attachment-preview-list">
        <div
          v-for="file in attachmentFiles"
          :key="getFileId(file)"
          class="attachment-preview-item"
        >
          <icon-file class="attachment-file-icon" />
          <div class="attachment-file-main">
            <div class="attachment-file-name">{{ getFileName(file) }}</div>
          </div>
          <a-button
            class="remove-attachment-btn"
            type="text"
            size="mini"
            @click="removeAttachment(getFileId(file))"
          >
            <template #icon><icon-close /></template>
          </a-button>
        </div>
      </div>
    </div>

    <div
      class="input-wrapper"
      :class="{ 'drag-over': isDragOver }"
      @drop.prevent="handleDrop"
      @dragover.prevent="handleDragOver"
      @dragleave.prevent="handleDragLeave"
    >
      <div class="textarea-wrapper">
        <a-textarea
          v-model="inputMessage"
          :placeholder="supportsVision ? text.visionPlaceholder : text.textPlaceholder"
          :disabled="isLoading"
          class="chat-input"
          :auto-size="{ minRows: 1, maxRows: 6 }"
          @keydown="handleKeyDown"
          @paste="handlePaste"
        />

        <div v-if="isDragOver && supportsVision" class="drag-overlay">
          <icon-image class="drag-icon" />
          <span>{{ text.dropToUpload }}</span>
        </div>
      </div>

      <TokenUsageIndicator
        v-if="contextTokenCount > 0 || contextLimit > 0"
        :current-tokens="contextTokenCount"
        :max-tokens="contextLimit"
      />

      <div class="input-actions">
        <input
          ref="fileInputRef"
          type="file"
          accept="image/jpeg,image/png,image/gif"
          multiple
          class="hidden-file-input"
          @change="handleFileInputChange"
        />
        <input
          ref="attachmentInputRef"
          type="file"
          multiple
          class="hidden-file-input"
          @change="handleAttachmentInputChange"
        />

        <a-button
          v-if="supportsVision && !isLoading"
          type="secondary"
          class="upload-button"
          @click="openFilePicker"
        >
          <template #icon><icon-image /></template>
          <span>{{ text.image }}</span>
        </a-button>
        <a-dropdown v-if="!isLoading" trigger="click">
          <a-button
            type="secondary"
            class="upload-button"
            :loading="isUploadingAttachments"
          >
            <template #icon><icon-upload /></template>
            <span>{{ text.attachment }}</span>
          </a-button>
          <template #content>
            <a-doption @click="openManagedFileChoose">{{ text.chooseManagedFile }}</a-doption>
            <a-doption @click="openAttachmentPicker">{{ text.uploadLocalFile }}</a-doption>
          </template>
        </a-dropdown>

        <a-button
          v-if="!isLoading"
          type="primary"
          class="send-button"
          @click="handleSendMessage"
        >
          <template #icon><icon-send /></template>
          <span>{{ text.send }}</span>
        </a-button>
        <a-button
          v-else
          type="primary"
          status="danger"
          class="stop-button"
          @click="handleStopGeneration"
        >
          <template #icon><icon-record-stop /></template>
          <span>{{ text.stop }}</span>
        </a-button>
      </div>
    </div>

    <a-modal
      v-model:visible="fileChooseVisible"
      :title="text.chooseManagedFile"
      :footer="false"
      width="720px"
    >
      <a-input-search
        v-model="fileChooseKeyword"
        :placeholder="text.fileSearchPlaceholder"
        allow-clear
        class="attachment-search"
        @search="loadManagedFilesForChoose"
        @clear="loadManagedFilesForChoose"
      />
      <a-table
        :loading="fileChooseLoading"
        :data="fileChooseFiles"
        row-key="id"
        :pagination="false"
        size="small"
      >
        <template #columns>
          <a-table-column :title="text.fileName" data-index="name">
            <template #cell="{ record }">{{ getFileName(record) }}</template>
          </a-table-column>
          <a-table-column :title="text.fileType" data-index="mime_type" :width="160" />
          <a-table-column :title="text.fileSize" data-index="size" :width="110">
            <template #cell="{ record }">{{ formatFileSize(record.size) }}</template>
          </a-table-column>
          <a-table-column :title="text.action" :width="90">
            <template #cell="{ record }">
              <a-button size="mini" type="primary" @click="chooseManagedFile(record)">{{ text.choose }}</a-button>
            </template>
          </a-table-column>
        </template>
      </a-table>
    </a-modal>
  </div>
</template>

<script setup lang="ts">
import { ref, computed } from 'vue';
import {
  Textarea as ATextarea,
  Button as AButton,
  Message
} from '@arco-design/web-vue';
import { IconImage, IconClose, IconReply, IconSend, IconRecordStop, IconUpload, IconFile } from '@arco-design/web-vue/es/icon';
import TokenUsageIndicator from './TokenUsageIndicator.vue';
import { useAppI18n } from '@/composables/useAppI18n';
import { useProjectStore } from '@/store/projectStore';
import { fileService } from '@/features/file-management/services/fileService';
import type { FileAsset } from '@/features/file-management/types';

interface ChatMessage {
  content: string;
  isUser: boolean;
  time: string;
  messageType?: 'human' | 'ai' | 'tool' | 'system' | 'agent_step' | 'step_separator';
  imageBase64?: string;
  imageDataUrl?: string;
  imageBase64List?: string[];
  imageDataUrls?: string[];
}

interface Props {
  isLoading: boolean;
  supportsVision?: boolean;
  contextTokenCount?: number;
  contextLimit?: number;
  quotedMessage?: ChatMessage | null;
}

const props = withDefaults(defineProps<Props>(), {
  supportsVision: false,
  contextTokenCount: 0,
  contextLimit: 128000,
  quotedMessage: null
});
const { isEnglish } = useAppI18n();
const projectStore = useProjectStore();
const text = computed(() => (
  isEnglish.value
    ? {
        selectedImages: (count: number) => `${count} image(s) selected`,
        selectedAttachments: (count: number) => `${count} attachment(s) selected`,
        clear: 'Clear',
        previewImageAlt: (index: number) => `Preview image ${index}`,
        visionPlaceholder: 'Type a message, drag, paste, or select images... (Shift+Enter newline, Enter send)',
        textPlaceholder: 'Type your message... (Shift+Enter newline, Enter send)',
        dropToUpload: 'Release to upload images',
        image: 'Image',
        attachment: 'Attachment',
        chooseManagedFile: 'Choose from files',
        uploadLocalFile: 'Upload local file',
        fileSearchPlaceholder: 'Search file name/type',
        fileName: 'File name',
        fileType: 'Type',
        fileSize: 'Size',
        action: 'Action',
        choose: 'Choose',
        send: 'Send',
        stop: 'Stop',
        emptyMessageOrImage: 'Enter a message or upload images',
        modelNoVision: 'Current AI model does not support image input.\nRemove images or switch to a multimodal model.\n(Recommended: GPT-4V, Claude 3, Gemini Vision, Qwen-VL)',
        imageProcessFailed: 'Image processing failed, please retry',
        viewImagePrompt: 'Please check the image',
        viewAttachmentPrompt: 'Please check the attachment',
        onlyImageAllowed: 'Only image files are allowed',
        imageTooLarge: 'Image size cannot exceed 10MB',
        imageFormatInvalid: 'Only JPG, PNG, and GIF images are supported',
        duplicateImage: (name: string) => `${name} already exists, no need to upload again`,
        previewFailed: 'Failed to generate image preview, please retry',
        modelNoVisionShort: 'Current AI model does not support image input.\nSelect a multimodal model in model management (such as GPT-4V, Claude 3, Qwen-VL).',
        modelNoVisionPaste: 'Current AI model does not support image input.\nSelect a multimodal model in model management.\n(Recommended: GPT-4V, Claude 3, Gemini Vision, Qwen-VL)',
        imagePasted: (count: number) => (count === 1 ? 'Image pasted' : `${count} images pasted`),
        selectProjectFirst: 'Please select a project first',
        attachmentUploadSuccess: (count: number) => `${count} attachment(s) uploaded`,
        attachmentUploadFailed: 'Attachment upload failed',
        attachmentLoadFailed: 'Failed to load files',
        attachmentLimitExceeded: (limit: number) => `Up to ${limit} attachments can be selected`,
        attachmentAlreadySelected: 'This file is already selected',
        attachmentTooLarge: 'File size cannot exceed 100MB',
      }
    : {
        selectedImages: (count: number) => `已选择 ${count} 张图片`,
        selectedAttachments: (count: number) => `已选择 ${count} 个附件`,
        clear: '清空',
        previewImageAlt: (index: number) => `预览图片 ${index}`,
        visionPlaceholder: '输入消息、拖拽、粘贴或选择图片... (Shift+Enter换行，Enter发送)',
        textPlaceholder: '请输入你的消息... (Shift+Enter换行，Enter发送)',
        dropToUpload: '释放以上传图片',
        image: '图片',
        attachment: '附件',
        chooseManagedFile: '文件管理选择',
        uploadLocalFile: '上传本地文件',
        fileSearchPlaceholder: '搜索文件名/类型',
        fileName: '文件名',
        fileType: '类型',
        fileSize: '大小',
        action: '操作',
        choose: '选择',
        send: '发送',
        stop: '停止',
        emptyMessageOrImage: '请输入消息或上传图片！',
        modelNoVision: '❌ 当前AI模型不支持图片输入\n请先移除图片或切换到支持多模态的模型\n（推荐：GPT-4V、Claude 3、Gemini Vision、Qwen-VL）',
        imageProcessFailed: '图片处理失败，请重试',
        viewImagePrompt: '请查看图片',
        viewAttachmentPrompt: '请查看附件',
        onlyImageAllowed: '只能上传图片文件',
        imageTooLarge: '图片大小不能超过10MB',
        imageFormatInvalid: '仅支持JPG、PNG、GIF格式的图片',
        duplicateImage: (name: string) => `${name} 已存在，无需重复上传`,
        previewFailed: '图片预览生成失败，请重试',
        modelNoVisionShort: '💡 当前AI模型不支持图片输入\n请在模型管理中选择支持多模态的模型（如GPT-4V、Claude 3、Qwen-VL等）',
        modelNoVisionPaste: '💡 当前AI模型不支持图片输入\n请在模型管理中选择支持多模态的模型\n（推荐：GPT-4V、Claude 3、Gemini Vision、Qwen-VL）',
        imagePasted: (count: number) => (count === 1 ? '图片已粘贴' : `已粘贴 ${count} 张图片`),
        selectProjectFirst: '请先选择项目',
        attachmentUploadSuccess: (count: number) => `已上传 ${count} 个附件`,
        attachmentUploadFailed: '附件上传失败',
        attachmentLoadFailed: '加载文件失败',
        attachmentLimitExceeded: (limit: number) => `单次最多选择 ${limit} 个附件`,
        attachmentAlreadySelected: '该文件已选择',
        attachmentTooLarge: '文件大小不能超过 100MB',
      }
));

const emit = defineEmits<{
  'send-message': [data: {
    message: string;
    image?: string;
    imageDataUrl?: string;
    images?: string[];
    imageDataUrls?: string[];
    quotedMessage?: ChatMessage | null;
    file_ids?: number[];
    files?: FileAsset[];
  }];
  'clear-quote': [];
  'stop-generation': [];
}>();

const truncateQuote = (text: string): string => {
  const maxLen = 80;
  const singleLine = text.replace(/\n/g, ' ').trim();
  return singleLine.length > maxLen ? singleLine.slice(0, maxLen) + '...' : singleLine;
};

const inputMessage = ref('');
const imageFiles = ref<File[]>([]);
const imagePreviews = ref<string[]>([]);
const attachmentFileIds = ref<number[]>([]);
const attachmentFiles = ref<FileAsset[]>([]);
const isUploadingAttachments = ref(false);
const fileInputRef = ref<HTMLInputElement | null>(null);
const attachmentInputRef = ref<HTMLInputElement | null>(null);
const isDragOver = ref(false);
const fileChooseVisible = ref(false);
const fileChooseLoading = ref(false);
const fileChooseKeyword = ref('');
const fileChooseFiles = ref<FileAsset[]>([]);
const maxAttachmentCount = 5;
const maxAttachmentSize = 100 * 1024 * 1024;

const handleStopGeneration = () => {
  emit('stop-generation');
};

const removeImage = (index: number) => {
  imageFiles.value = imageFiles.value.filter((_, fileIndex) => fileIndex !== index);
  imagePreviews.value = imagePreviews.value.filter((_, previewIndex) => previewIndex !== index);
};

const clearImages = () => {
  imageFiles.value = [];
  imagePreviews.value = [];
  if (fileInputRef.value) {
    fileInputRef.value.value = '';
  }
};

const getFileId = (file: FileAsset) => Number(file.file_id || file.id);

const getFileName = (file: Partial<FileAsset>) => file.name || file.original_name || `file-${file.id || file.file_id || ''}`;

const formatFileSize = (size?: number) => {
  if (!Number.isFinite(size || 0) || !size) return '0 B';
  if (size < 1024) return `${size} B`;
  if (size < 1024 * 1024) return `${(size / 1024).toFixed(1)} KB`;
  if (size < 1024 * 1024 * 1024) return `${(size / 1024 / 1024).toFixed(1)} MB`;
  return `${(size / 1024 / 1024 / 1024).toFixed(1)} GB`;
};

const normalizeUploadedFiles = (payload: any): FileAsset[] => {
  const list = Array.isArray(payload) ? payload : [payload];
  return list.filter((item): item is FileAsset => Boolean(item && (item.id || item.file_id)));
};

const canAddAttachmentCount = (count: number) => {
  if (attachmentFiles.value.length + count > maxAttachmentCount) {
    Message.warning(text.value.attachmentLimitExceeded(maxAttachmentCount));
    return false;
  }
  return true;
};

const addAttachmentFiles = (files: FileAsset[]) => {
  const incoming = files.filter((file) => Boolean(getFileId(file)));
  if (!incoming.length) return;

  const existingIds = new Set(attachmentFiles.value.map(getFileId));
  const uniqueIncoming = incoming.filter((file) => !existingIds.has(getFileId(file)));

  if (uniqueIncoming.length === 0) {
    Message.warning(text.value.attachmentAlreadySelected);
    return;
  }

  if (!canAddAttachmentCount(uniqueIncoming.length)) {
    return;
  }

  attachmentFiles.value = [...attachmentFiles.value, ...uniqueIncoming];
  attachmentFileIds.value = attachmentFiles.value.map(getFileId);
};

const removeAttachment = (fileId: number) => {
  attachmentFiles.value = attachmentFiles.value.filter((file) => getFileId(file) !== fileId);
  attachmentFileIds.value = attachmentFiles.value.map(getFileId);
};

const clearAttachments = () => {
  attachmentFiles.value = [];
  attachmentFileIds.value = [];
};

const handleSendMessage = async () => {
  const message = inputMessage.value.trim();

  if (!message && imageFiles.value.length === 0 && attachmentFileIds.value.length === 0) {
    Message.warning(text.value.emptyMessageOrImage);
    return;
  }

  if (imageFiles.value.length > 0 && !props.supportsVision) {
    Message.error({
      content: text.value.modelNoVision,
      duration: 5000
    });
    return;
  }

  let imageBase64List: string[] = [];
  let imageDataUrlList: string[] = [];

  if (imageFiles.value.length > 0) {
    try {
      const results = await Promise.all(imageFiles.value.map((file) => fileToBase64WithDataUrl(file)));
      imageBase64List = results.map((result) => result.base64);
      imageDataUrlList = results.map((result) => result.dataUrl);
    } catch {
      Message.error(text.value.imageProcessFailed);
      return;
    }
  }

  emit('send-message', {
    message: message || (imageFiles.value.length > 0 ? text.value.viewImagePrompt : text.value.viewAttachmentPrompt),
    image: imageBase64List[0],
    imageDataUrl: imageDataUrlList[0],
    images: imageBase64List,
    imageDataUrls: imageDataUrlList,
    quotedMessage: props.quotedMessage,
    file_ids: attachmentFileIds.value,
    files: attachmentFiles.value
  });

  inputMessage.value = '';
  clearImages();
  clearAttachments();
};

const fileToBase64WithDataUrl = (file: File): Promise<{ base64: string; dataUrl: string }> => {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => {
      const dataUrl = reader.result as string;
      const base64 = dataUrl.split(',')[1];
      resolve({ base64, dataUrl });
    };
    reader.onerror = reject;
    reader.readAsDataURL(file);
  });
};

const readFileAsDataUrl = (file: File): Promise<string> => {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(reader.result as string);
    reader.onerror = reject;
    reader.readAsDataURL(file);
  });
};

const isDuplicateImageFile = (file: File) => {
  return imageFiles.value.some((existingFile) => (
    existingFile.name === file.name
    && existingFile.size === file.size
    && existingFile.lastModified === file.lastModified
  ));
};

const handleKeyDown = (event: KeyboardEvent) => {
  if (event.key === 'Enter' && !event.shiftKey) {
    event.preventDefault();
    void handleSendMessage();
  }
};

const validateImageFile = (file: File) => {
  if (!file.type.startsWith('image/')) {
    Message.error(text.value.onlyImageAllowed);
    return false;
  }

  const maxSize = 10 * 1024 * 1024;
  if (file.size > maxSize) {
    Message.error(text.value.imageTooLarge);
    return false;
  }

  const validTypes = ['image/jpeg', 'image/png', 'image/gif'];
  if (!validTypes.includes(file.type)) {
    Message.error(text.value.imageFormatInvalid);
    return false;
  }

  if (isDuplicateImageFile(file)) {
    Message.warning(text.value.duplicateImage(file.name));
    return false;
  }

  return true;
};

const appendImageFiles = async (files: File[]) => {
  const validFiles = files.filter(validateImageFile);
  if (validFiles.length === 0) {
    return;
  }

  try {
    const previews = await Promise.all(validFiles.map((file) => readFileAsDataUrl(file)));
    imageFiles.value = [...imageFiles.value, ...validFiles];
    imagePreviews.value = [...imagePreviews.value, ...previews];
  } catch {
    Message.error(text.value.previewFailed);
  }
};

const openFilePicker = () => {
  fileInputRef.value?.click();
};

const openAttachmentPicker = () => {
  attachmentInputRef.value?.click();
};

const openManagedFileChoose = async () => {
  if (!projectStore.currentProjectId) {
    Message.warning(text.value.selectProjectFirst);
    return;
  }
  fileChooseVisible.value = true;
  await loadManagedFilesForChoose();
};

const loadManagedFilesForChoose = async () => {
  if (!projectStore.currentProjectId) return;
  fileChooseLoading.value = true;
  try {
    const res: any = await fileService.list(projectStore.currentProjectId, {
      page: 1,
      page_size: 50,
      search: fileChooseKeyword.value || undefined,
      ordering: '-created_at',
    });
    const payload = res?.data?.data || res?.data || res;
    fileChooseFiles.value = Array.isArray(payload) ? payload : (payload.results || payload.data || []);
  } catch (error: any) {
    Message.error(error?.message || text.value.attachmentLoadFailed);
  } finally {
    fileChooseLoading.value = false;
  }
};

const chooseManagedFile = (file: FileAsset) => {
  addAttachmentFiles([file]);
  fileChooseVisible.value = false;
};

const validateAttachmentFile = (file: File) => {
  if (file.size > maxAttachmentSize) {
    Message.warning(text.value.attachmentTooLarge);
    return false;
  }
  return true;
};

const handleAttachmentInputChange = async (event: Event) => {
  const input = event.target as HTMLInputElement;
  const files = input.files ? Array.from(input.files) : [];
  if (!files.length) return;
  if (!projectStore.currentProjectId) {
    Message.warning(text.value.selectProjectFirst);
    input.value = '';
    return;
  }
  const validFiles = files.filter(validateAttachmentFile);
  if (!validFiles.length) {
    input.value = '';
    return;
  }
  if (!canAddAttachmentCount(validFiles.length)) {
    input.value = '';
    return;
  }
  isUploadingAttachments.value = true;
  try {
    const res: any = await fileService.upload(projectStore.currentProjectId, validFiles);
    const payload = res?.data?.data || res?.data || res;
    const uploaded = normalizeUploadedFiles(payload);
    addAttachmentFiles(uploaded);
    Message.success(text.value.attachmentUploadSuccess(uploaded.length));
  } catch (error: any) {
    Message.error(error?.message || text.value.attachmentUploadFailed);
  } finally {
    isUploadingAttachments.value = false;
    input.value = '';
  }
};

const handleFileInputChange = async (event: Event) => {
  const input = event.target as HTMLInputElement;
  const files = input.files ? Array.from(input.files) : [];
  if (!files.length) {
    return;
  }

  await appendImageFiles(files);
  input.value = '';
};

const handleDragOver = (_e: DragEvent) => {
  if (!props.supportsVision || props.isLoading) return;
  isDragOver.value = true;
};

const handleDragLeave = (_e: DragEvent) => {
  isDragOver.value = false;
};

const handleDrop = (e: DragEvent) => {
  isDragOver.value = false;

  if (!props.supportsVision) {
    Message.warning({
      content: text.value.modelNoVisionShort,
      duration: 4000
    });
    return;
  }

  if (props.isLoading) return;

  const files = e.dataTransfer?.files ? Array.from(e.dataTransfer.files) : [];
  if (!files.length) return;

  void appendImageFiles(files);
};

const handlePaste = (e: ClipboardEvent) => {
  if (props.isLoading) return;

  const items = e.clipboardData?.items;
  if (!items) return;

  const imageFilesFromClipboard: File[] = [];

  for (let i = 0; i < items.length; i++) {
    const item = items[i];
    if (!item.type.startsWith('image/')) {
      continue;
    }
    const file = item.getAsFile();
    if (file) {
      imageFilesFromClipboard.push(file);
    }
  }

  if (!imageFilesFromClipboard.length) {
    return;
  }

  e.preventDefault();

  if (!props.supportsVision) {
    Message.warning({
      content: text.value.modelNoVisionPaste,
      duration: 4000
    });
    return;
  }

  void appendImageFiles(imageFilesFromClipboard);
  Message.success(text.value.imagePasted(imageFilesFromClipboard.length));
};
</script>

<style scoped>
.hidden-file-input {
  display: none;
}

.chat-input-container {
  padding: 16px 20px;
  background-color: white;
  border-top: 1px solid #e5e6eb;
}

.quote-preview-wrapper {
  margin-bottom: 12px;
}

.quote-preview {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 8px 12px;
  background: linear-gradient(135deg, #f0f5ff 0%, #e8f3ff 100%);
  border-left: 3px solid #165dff;
  border-radius: 0 8px 8px 0;
}

.quote-icon {
  color: #165dff;
  font-size: 14px;
  flex-shrink: 0;
}

.quote-text {
  flex: 1;
  font-size: 13px;
  color: #4e5969;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.quote-close-btn {
  flex-shrink: 0;
  color: #86909c !important;
}

.quote-close-btn:hover {
  color: #f53f3f !important;
}

.image-preview-wrapper {
  margin-bottom: 12px;
}

.attachment-preview-wrapper {
  margin-bottom: 12px;
}

.attachment-preview-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 8px;
}

.attachment-preview-count {
  font-size: 12px;
  color: #4e5969;
  font-weight: 500;
}

.clear-attachments-btn {
  color: #86909c !important;
}

.attachment-preview-list {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
}

.attachment-preview-item {
  display: flex;
  align-items: center;
  gap: 8px;
  max-width: min(100%, 320px);
  min-width: 220px;
  padding: 8px 10px;
  border: 1px solid #e5e6eb;
  border-radius: 8px;
  background: #f7f8fa;
}

.attachment-file-icon {
  flex-shrink: 0;
  color: #165dff;
  font-size: 18px;
}

.attachment-file-main {
  min-width: 0;
  flex: 1;
}

.attachment-file-name {
  font-size: 13px;
  color: #1d2129;
  font-weight: 500;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.remove-attachment-btn {
  flex-shrink: 0;
  color: #86909c !important;
}

.remove-attachment-btn:hover {
  color: #f53f3f !important;
}

.attachment-search {
  margin-bottom: 12px;
}

.image-preview-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 8px;
}

.image-preview-count {
  font-size: 12px;
  color: #4e5969;
  font-weight: 500;
}

.clear-images-btn {
  color: #86909c !important;
}

.image-preview-list {
  display: flex;
  flex-wrap: wrap;
  gap: 10px;
}

.image-preview {
  position: relative;
  width: 108px;
  height: 108px;
  border-radius: 10px;
  overflow: hidden;
  box-shadow: 0 2px 8px rgba(0, 0, 0, 0.1);
  background: #f7f8fa;
}

.image-preview img {
  width: 100%;
  height: 100%;
  object-fit: cover;
  display: block;
}

.image-order-badge {
  position: absolute;
  left: 6px;
  bottom: 6px;
  min-width: 20px;
  height: 20px;
  padding: 0 6px;
  border-radius: 999px;
  background: rgba(0, 0, 0, 0.65);
  color: white;
  font-size: 11px;
  line-height: 20px;
  text-align: center;
}

.remove-image-btn {
  position: absolute;
  top: 4px;
  right: 4px;
  background-color: rgba(0, 0, 0, 0.6);
  color: white;
  border-radius: 50%;
  width: 24px;
  height: 24px;
  padding: 0;
  display: flex;
  align-items: center;
  justify-content: center;
  transition: all 0.2s;
}

.remove-image-btn:hover {
  background-color: rgba(0, 0, 0, 0.8);
}

.input-wrapper {
  display: flex;
  align-items: center;
  width: 100%;
  gap: 8px;
  position: relative;
  transition: all 0.3s;
}

.input-wrapper.drag-over {
  transform: scale(1.02);
}

.textarea-wrapper {
  position: relative;
  flex: 1;
}

.chat-input {
  width: 100%;
  border-radius: 12px;
  background-color: #f2f3f5;
  transition: all 0.2s;
  resize: none;
  min-height: 36px;
}

.chat-input:hover, .chat-input:focus {
  background-color: white;
  box-shadow: 0 2px 8px rgba(0, 0, 0, 0.1);
}

.chat-input :deep(.arco-textarea) {
  border-radius: 12px;
  padding: 8px 12px;
  line-height: 20px;
  min-height: 36px;
}

.drag-overlay {
  position: absolute;
  top: 0;
  left: 0;
  right: 0;
  bottom: 0;
  background: linear-gradient(135deg, rgba(22, 93, 255, 0.1), rgba(22, 93, 255, 0.05));
  border: 2px dashed #165dff;
  border-radius: 12px;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 8px;
  pointer-events: none;
  z-index: 10;
  animation: pulse 1.5s ease-in-out infinite;
}

.drag-icon {
  font-size: 32px;
  color: #165dff;
}

.drag-overlay span {
  font-size: 14px;
  color: #165dff;
  font-weight: 500;
}

@keyframes pulse {
  0%, 100% {
    opacity: 1;
    transform: scale(1);
  }
  50% {
    opacity: 0.8;
    transform: scale(1.02);
  }
}

.input-actions {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-shrink: 0;
}

.upload-button,
.send-button,
.stop-button {
  display: flex;
  align-items: center;
  gap: 4px;
  border-radius: 20px;
  padding: 0 16px;
  height: 36px;
  flex-shrink: 0;
}

.stop-button {
  animation: pulse-stop 1.5s ease-in-out infinite;
}

@keyframes pulse-stop {
  0%, 100% {
    opacity: 1;
  }
  50% {
    opacity: 0.7;
  }
}

.icon-send {
  margin-right: 4px;
  font-size: 16px;
}
</style>
