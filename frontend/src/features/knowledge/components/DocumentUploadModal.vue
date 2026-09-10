<template>
  <a-modal
    :visible="visible"
    :title="text.uploadDocument"
    :width="600"
    @ok="handleSubmit"
    @cancel="handleCancel"
    :confirm-loading="loading"
  >
    <a-form
      ref="formRef"
      :model="formData"
      :rules="rules"
      layout="vertical"
    >
      <a-form-item :label="text.uploadMethod" field="uploadType">
        <a-radio-group v-model="formData.uploadType" @change="handleUploadTypeChange">
          <a-radio value="file">{{ text.fileUpload }}</a-radio>
          <a-radio value="text">{{ text.textContent }}</a-radio>
          <a-radio value="url">{{ text.webLink }}</a-radio>
        </a-radio-group>
      </a-form-item>

      <a-form-item :label="text.documentTitle" field="title">
        <a-input
          v-model="formData.title"
          :placeholder="text.documentTitlePlaceholder"
          :max-length="200"
        />
      </a-form-item>

      <!-- 文件上传 -->
      <template v-if="formData.uploadType === 'file'">
        <a-form-item :label="text.selectFile" field="file">
          <div class="file-upload-container">
            <input
              ref="fileInputRef"
              type="file"
              accept=".pdf,.docx,.doc,.xlsx,.xls,.pptx,.txt,.md,.html,.htm"
              style="display: none"
              @change="handleFileInputChange"
            />
            <div class="upload-area" @click="triggerFileInput">
              <icon-upload />
              <div class="upload-text">
                <div>{{ text.clickToSelectFile }}</div>
                <div class="upload-tip">
                  {{ text.supportedFormats }}
                </div>
              </div>
            </div>
            <div v-if="formData.file" class="selected-file">
              <div class="file-info">
                <icon-file />
                <span class="file-name">{{ formData.file.name }}</span>
                <span class="file-size">({{ formatFileSize(formData.file.size) }})</span>
                <a-button type="text" size="mini" @click="removeFile">
                  <icon-close />
                </a-button>
              </div>
            </div>
          </div>
        </a-form-item>
      </template>

      <!-- 文本内容 -->
      <template v-if="formData.uploadType === 'text'">
        <a-form-item :label="text.textContent" field="content">
          <a-textarea
            v-model="formData.content"
            :placeholder="text.textContentPlaceholder"
            :rows="10"
            :max-length="50000"
            show-word-limit
          />
        </a-form-item>
      </template>

      <!-- 网页链接 -->
      <template v-if="formData.uploadType === 'url'">
        <a-form-item :label="text.webLink" field="url">
          <a-input
            v-model="formData.url"
            :placeholder="text.webLinkPlaceholder"
          />
        </a-form-item>
      </template>
    </a-form>

    <div v-if="uploadProgress > 0 && uploadProgress < 100" class="upload-progress">
      <a-progress :percent="uploadProgress" />
      <div class="progress-text">{{ text.uploadingDocument }}</div>
    </div>
  </a-modal>
</template>

<script setup lang="ts">
import { ref, reactive, computed, watch } from 'vue';
import { Message } from '@arco-design/web-vue';
import { IconUpload, IconFile, IconClose } from '@arco-design/web-vue/es/icon';
import { KnowledgeService } from '../services/knowledgeService';
import type { UploadDocumentRequest, DocumentType } from '../types/knowledge';
import { useAppI18n } from '@/composables/useAppI18n';

interface Props {
  visible: boolean;
  knowledgeBaseId: string;
}

const props = defineProps<Props>();
const emit = defineEmits<{
  submit: [];
  cancel: [];
}>();
const { isEnglish } = useAppI18n();

const text = computed(() => (
  isEnglish.value
    ? {
        uploadDocument: 'Upload document',
        uploadMethod: 'Upload method',
        fileUpload: 'File upload',
        textContent: 'Text content',
        webLink: 'Web link',
        documentTitle: 'Document title',
        documentTitlePlaceholder: 'Enter document title',
        selectFile: 'Select file',
        clickToSelectFile: 'Click to select a file',
        supportedFormats: 'Supports PDF, Word, Excel, PPT, Text, Markdown, and HTML',
        textContentPlaceholder: 'Enter or paste text content...',
        webLinkPlaceholder: 'Enter URL, e.g. https://example.com',
        uploadingDocument: 'Uploading document...',
        validateTitleRequired: 'Please enter document title',
        validateTitleMin: 'Document title cannot be empty',
        validateTitleMax: 'Document title must be at most 200 characters',
        validateFileRequired: 'Please select a file to upload',
        validateContentRequired: 'Please enter text content',
        validateContentMin: 'Text content must be at least 10 characters',
        validateContentMax: 'Text content must be at most 50000 characters',
        validateUrlRequired: 'Please enter a URL',
        validateUrlInvalid: 'Please enter a valid URL',
        unsupportedFileType: (extensions: string[]) => `Unsupported file format. Supported: ${extensions.join(', ')}`,
        formInvalid: 'Please check the form fields',
        uploadFailed: 'Failed to upload document',
      }
    : {
        uploadDocument: '上传文档',
        uploadMethod: '上传方式',
        fileUpload: '文件上传',
        textContent: '文本内容',
        webLink: '网页链接',
        documentTitle: '文档标题',
        documentTitlePlaceholder: '请输入文档标题',
        selectFile: '选择文件',
        clickToSelectFile: '点击选择文件',
        supportedFormats: '支持 PDF、Word、Excel、PPT、文本、Markdown、HTML 格式',
        textContentPlaceholder: '请输入或粘贴文本内容...',
        webLinkPlaceholder: '请输入网页链接，如：https://example.com',
        uploadingDocument: '正在上传文档...',
        validateTitleRequired: '请输入文档标题',
        validateTitleMin: '文档标题不能为空',
        validateTitleMax: '文档标题不能超过200个字符',
        validateFileRequired: '请选择要上传的文件',
        validateContentRequired: '请输入文本内容',
        validateContentMin: '文本内容至少10个字符',
        validateContentMax: '文本内容不能超过50000个字符',
        validateUrlRequired: '请输入网页链接',
        validateUrlInvalid: '请输入有效的网页链接',
        unsupportedFileType: (extensions: string[]) => `不支持的文件格式，仅支持：${extensions.join(', ')}`,
        formInvalid: '请检查表单填写是否正确',
        uploadFailed: '上传文档失败',
      }
));

const formRef = ref();
const fileInputRef = ref<HTMLInputElement>();
const loading = ref(false);
const uploadProgress = ref(0);

// 表单数据
const formData = reactive({
  uploadType: 'file' as 'file' | 'text' | 'url',
  title: '',
  file: null as File | null,
  content: '',
  url: '',
});

// 表单验证规则
const rules = computed(() => {
  const baseRules = {
    title: [
      { required: true, message: text.value.validateTitleRequired },
      { minLength: 1, message: text.value.validateTitleMin },
      { maxLength: 200, message: text.value.validateTitleMax },
    ],
  };

  if (formData.uploadType === 'file') {
    return {
      ...baseRules,
      file: [
        { required: true, message: text.value.validateFileRequired },
      ],
    };
  } else if (formData.uploadType === 'text') {
    return {
      ...baseRules,
      content: [
        { required: true, message: text.value.validateContentRequired },
        { minLength: 10, message: text.value.validateContentMin },
        { maxLength: 50000, message: text.value.validateContentMax },
      ],
    };
  } else if (formData.uploadType === 'url') {
    return {
      ...baseRules,
      url: [
        { required: true, message: text.value.validateUrlRequired },
        {
          type: 'url',
          message: text.value.validateUrlInvalid,
          validator: (value: string) => {
            try {
              new URL(value);
              return true;
            } catch {
              return false;
            }
          }
        },
      ],
    };
  }

  return baseRules;
});

// 监听弹窗显示状态
watch(() => props.visible, (visible) => {
  if (visible) {
    resetForm();
  }
});

// 方法
const resetForm = () => {
  Object.assign(formData, {
    uploadType: 'file',
    title: '',
    file: null,
    content: '',
    url: '',
  });
  uploadProgress.value = 0;
  if (fileInputRef.value) {
    fileInputRef.value.value = '';
  }
  formRef.value?.clearValidate();
};

const handleUploadTypeChange = () => {
  // 切换上传方式时清空相关字段
  formData.file = null;
  formData.content = '';
  formData.url = '';
  if (fileInputRef.value) {
    fileInputRef.value.value = '';
  }
  formRef.value?.clearValidate();
};

// 支持的文件扩展名（与后端 DocumentLoader 支持的格式保持一致）
const ALLOWED_EXTENSIONS = ['pdf', 'docx', 'doc', 'xlsx', 'xls', 'pptx', 'txt', 'md', 'html', 'htm'];

const validateFileExtension = (file: File): boolean => {
  const ext = file.name.split('.').pop()?.toLowerCase() || '';
  return ALLOWED_EXTENSIONS.includes(ext);
};

const triggerFileInput = () => {
  fileInputRef.value?.click();
};

const handleFileInputChange = (event: Event) => {
  const target = event.target as HTMLInputElement;
  const file = target.files?.[0];

  if (file) {
    // 验证文件扩展名
    if (!validateFileExtension(file)) {
      Message.error(text.value.unsupportedFileType(ALLOWED_EXTENSIONS));
      target.value = '';
      return;
    }

    formData.file = file;

    // 如果没有设置标题，使用文件名作为默认标题
    if (!formData.title) {
      const fileName = file.name;
      const nameWithoutExt = fileName.substring(0, fileName.lastIndexOf('.')) || fileName;
      formData.title = nameWithoutExt;
    }
  }
};

const removeFile = () => {
  formData.file = null;
  if (fileInputRef.value) {
    fileInputRef.value.value = '';
  }
};

const formatFileSize = (bytes: number): string => {
  if (bytes === 0) return '0 B';
  const k = 1024;
  const sizes = ['B', 'KB', 'MB', 'GB'];
  const i = Math.floor(Math.log(bytes) / Math.log(k));
  return parseFloat((bytes / Math.pow(k, i)).toFixed(2)) + ' ' + sizes[i];
};

const getDocumentType = (uploadType: string, file?: File): DocumentType => {
  if (uploadType === 'text') return 'txt';
  if (uploadType === 'url') return 'url';

  if (file) {
    const ext = file.name.split('.').pop()?.toLowerCase();
    const typeMap: Record<string, DocumentType> = {
      'pdf': 'pdf',
      'docx': 'docx',
      'doc': 'doc',
      'xlsx': 'xlsx',
      'xls': 'xls',
      'pptx': 'pptx',
      'txt': 'txt',
      'md': 'md',
      'html': 'html',
      'htm': 'html',
    };
    return typeMap[ext || ''] || 'txt';
  }

  return 'txt';
};

const handleSubmit = async () => {
  try {
    // Arco Design 的 validate 方法：成功时返回 undefined，失败时抛出错误
    await formRef.value?.validate();

    // 验证文件上传模式下是否选择了文件
    if (formData.uploadType === 'file' && !formData.file) {
      Message.error(text.value.validateFileRequired);
      return;
    }

    loading.value = true;
    uploadProgress.value = 0;

    const uploadData: UploadDocumentRequest = {
      knowledge_base: props.knowledgeBaseId,
      title: formData.title,
      document_type: getDocumentType(formData.uploadType, formData.file || undefined),
    };

    if (formData.uploadType === 'file' && formData.file) {
      uploadData.file = formData.file;
    } else if (formData.uploadType === 'text') {
      uploadData.content = formData.content;
    } else if (formData.uploadType === 'url') {
      uploadData.url = formData.url;
    }

    // 模拟上传进度
    const progressInterval = setInterval(() => {
      if (uploadProgress.value < 90) {
        uploadProgress.value += Math.random() * 20;
      }
    }, 200);

    await KnowledgeService.uploadDocument(uploadData);

    clearInterval(progressInterval);
    uploadProgress.value = 100;

    setTimeout(() => {
      emit('submit');
    }, 500);

  } catch (error: any) {
    console.error('上传文档失败:', error);
    // 检查是否是表单验证错误
    if (error && typeof error === 'object' && 'errorFields' in error) {
      Message.error(text.value.formInvalid);
    } else {
      // 显示具体的错误消息
      const errorMessage = error?.message || text.value.uploadFailed;
      Message.error(errorMessage);
    }
    uploadProgress.value = 0;
  } finally {
    loading.value = false;
  }
};

const handleCancel = () => {
  emit('cancel');
};
</script>

<style scoped>
.upload-area {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  padding: 40px 20px;
  border: 2px dashed #d9d9d9;
  border-radius: 6px;
  background-color: #fafafa;
  cursor: pointer;
  transition: border-color 0.3s;
}

.upload-area:hover {
  border-color: #00a0e9;
}

.upload-text {
  margin-top: 12px;
  text-align: center;
}

.upload-text > div:first-child {
  font-size: 14px;
  color: var(--theme-text);
  margin-bottom: 4px;
}

.upload-tip {
  font-size: 12px;
  color: var(--theme-text-tertiary);
}

.upload-progress {
  margin-top: 16px;
  padding: 16px;
  background-color: color-mix(in srgb, var(--theme-surface-soft) 72%, white 28%);
  border-radius: 6px;
}

.progress-text {
  text-align: center;
  margin-top: 8px;
  font-size: 12px;
  color: var(--theme-text-secondary);
}

.file-upload-container {
  width: 100%;
}

.selected-file {
  margin-top: 12px;
  padding: 8px 12px;
  background: color-mix(in srgb, var(--theme-surface-soft) 72%, white 28%);
  border-radius: 6px;
  border: 1px solid var(--theme-border);
}

.file-info {
  display: flex;
  align-items: center;
  gap: 8px;
}

.file-name {
  flex: 1;
  font-size: 14px;
  color: var(--theme-text);
}

.file-size {
  font-size: 12px;
  color: var(--theme-text-secondary);
}

:deep(.arco-form-item-label) {
  font-weight: 500;
}
</style>
