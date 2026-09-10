<template>
  <div class="file-management-page">
    <a-page-header :title="pageTitle" :subtitle="pageSubtitle" :show-back="false">
      <template #extra>
        <a-space>
          <a-input-search
            v-model="searchKeyword"
            :placeholder="searchPlaceholder"
            allow-clear
            style="width: 260px"
            @search="loadFiles"
            @clear="loadFiles"
          />
          <input ref="uploadInputRef" type="file" multiple class="hidden-file-input" @change="handleUploadChange" />
          <a-button type="primary" :loading="uploading" @click="uploadInputRef?.click()">
            <template #icon><icon-upload /></template>
            {{ uploadText }}
          </a-button>
          <a-button @click="openSettings">
            <template #icon><icon-settings /></template>
            {{ settingsText }}
          </a-button>
          <a-button :loading="loading" @click="loadFiles">
            <template #icon><icon-refresh /></template>
            {{ refreshText }}
          </a-button>
        </a-space>
      </template>
    </a-page-header>

    <a-alert v-if="!projectStore.currentProjectId" type="warning" show-icon class="mb-16">
      {{ noProjectText }}
    </a-alert>

    <a-card :bordered="false" class="file-card">
      <a-table
        row-key="id"
        :loading="loading"
        :data="files"
        :pagination="pagination"
        @page-change="handlePageChange"
        @page-size-change="handlePageSizeChange"
      >
        <template #columns>
          <a-table-column title="ID" data-index="id" :width="90" />
          <a-table-column :title="nameText" data-index="name" :ellipsis="true" :tooltip="true" />
          <a-table-column :title="typeText" data-index="extension" :width="100">
            <template #cell="{ record }">
              <a-tag color="arcoblue">{{ formatExtension(record.extension, record.mime_type) }}</a-tag>
            </template>
          </a-table-column>
          <a-table-column :title="sizeText" data-index="size" :width="110">
            <template #cell="{ record }">{{ formatSize(record.size) }}</template>
          </a-table-column>
          <a-table-column :title="statusText" data-index="status" :width="100">
            <template #cell="{ record }">
              <a-tag :color="record.status === 'available' ? 'green' : 'orange'">{{ record.status }}</a-tag>
            </template>
          </a-table-column>
          <a-table-column :title="refsText" data-index="reference_count" :width="90">
            <template #cell="{ record }">
              <a-button type="text" size="mini" @click="openReferences(record)">
                {{ record.reference_count || 0 }}
              </a-button>
            </template>
          </a-table-column>
          <a-table-column :title="createdText" data-index="created_at" :width="190">
            <template #cell="{ record }">{{ formatDate(record.created_at) }}</template>
          </a-table-column>
          <a-table-column :title="actionsText" :width="230" fixed="right">
            <template #cell="{ record }">
              <a-space>
                <a-button size="mini" type="text" @click="previewFile(record)">{{ previewText }}</a-button>
                <a-button size="mini" type="text" @click="downloadFile(record)">{{ downloadText }}</a-button>
                <a-popconfirm :content="deleteConfirmText" @ok="deleteFile(record)">
                  <a-button size="mini" type="text" status="danger">{{ deleteText }}</a-button>
                </a-popconfirm>
              </a-space>
            </template>
          </a-table-column>
        </template>
      </a-table>
    </a-card>

    <a-modal v-model:visible="previewVisible" :title="previewTitle" :footer="false" width="80%">
      <div v-if="previewType === 'image'" class="preview-container image-container">
        <a-image :src="previewUrl" fit="contain" style="max-height: 70vh; width: 100%; border-radius: 4px;" show-loader />
      </div>
      <div v-else-if="previewType === 'pdf'" class="preview-container pdf-container">
        <iframe v-if="previewUrl" :src="previewUrl" class="preview-frame" />
      </div>
      <div v-else-if="previewType === 'text'" class="preview-container text-container">
        <a-spin :loading="loadingContent" style="width: 100%; min-height: 400px; display: flex; align-items: center; justify-content: center;">
          <div v-if="!loadingContent" class="text-preview-shell">
            <div class="text-preview-header">
              <a-space>
                <a-tag color="arcoblue">{{ previewMime }}</a-tag>
                <a-tag color="gray">{{ previewSize }}</a-tag>
              </a-space>
              <a-button size="mini" type="outline" @click="copyTextContent">
                <template #icon><icon-copy /></template>
                {{ copyBtnText }}
              </a-button>
            </div>
            <div class="editor-shell" style="border: 1px solid var(--color-border-2); border-radius: 4px; overflow: hidden; margin-top: 8px;">
              <MonacoEditor
                v-model:value="previewContent"
                :language="previewLanguage"
                :theme="editorTheme"
                :options="editorOptions"
                style="height: 55vh; width: 100%;"
              />
            </div>
          </div>
        </a-spin>
      </div>
      <a-empty v-else />
    </a-modal>

    <a-modal v-model:visible="settingsVisible" :title="settingsTitle" :confirm-loading="savingSettings" @ok="saveSettings">
      <a-alert type="info" show-icon class="mb-16">
        {{ settingsTipText }}
      </a-alert>
      <a-form :model="settingsForm" layout="vertical">
        <a-form-item :label="autoDeleteOnUnbindText">
          <a-switch v-model="settingsForm.auto_delete_on_unbind" />
          <span class="setting-desc">{{ autoDeleteOnUnbindDesc }}</span>
        </a-form-item>
        <a-form-item :label="autoDeleteZeroRefsText">
          <a-switch v-model="settingsForm.auto_delete_zero_refs" />
          <span class="setting-desc">{{ autoDeleteZeroRefsDesc }}</span>
        </a-form-item>
      </a-form>
      <template #footer>
        <a-space>
          <a-button :loading="cleaning" status="warning" @click="cleanupNow">{{ cleanupNowText }}</a-button>
          <a-button @click="settingsVisible = false">{{ cancelText }}</a-button>
          <a-button type="primary" :loading="savingSettings" @click="saveSettings">{{ saveText }}</a-button>
        </a-space>
      </template>
    </a-modal>

    <a-modal v-model:visible="referencesVisible" :title="referencesTitle" :footer="false" width="860px">
      <a-spin :loading="referencesLoading">
        <a-empty v-if="!referenceRows.length" :description="noReferencesText" />
        <a-table v-else :data="referenceRows" row-key="id" :pagination="false" size="small">
          <template #columns>
            <a-table-column :title="refTypeText" data-index="ref_type_label" :width="120">
              <template #cell="{ record }"><a-tag>{{ record.ref_type_label || record.ref_type }}</a-tag></template>
            </a-table-column>
            <a-table-column :title="refObjectText" data-index="object_name">
              <template #cell="{ record }">
                <a-tooltip :content="formatReferenceObject(record)" position="top">
                  <div class="ref-object-one-line">{{ formatReferenceObject(record) }}</div>
                </a-tooltip>
              </template>
            </a-table-column>
            <a-table-column :title="refDescText" data-index="description" :ellipsis="true" :tooltip="true" />
            <a-table-column :title="createdByText" data-index="created_by" :width="110" />
            <a-table-column :title="createdText" data-index="created_at" :width="180">
              <template #cell="{ record }">{{ formatDate(record.created_at) }}</template>
            </a-table-column>
          </template>
        </a-table>
      </a-spin>
    </a-modal>

  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, reactive, ref, watch } from 'vue';
import { Message } from '@arco-design/web-vue';
import { IconRefresh, IconUpload, IconSettings, IconCopy } from '@arco-design/web-vue/es/icon';
import { useProjectStore } from '@/store/projectStore';
import { useAppI18n } from '@/composables/useAppI18n';
import { fileService } from '../services/fileService';
import type { FileAsset, FileManagementSetting, FileReferenceDetail } from '../types';
import MonacoEditor from '@guolao/vue-monaco-editor';
import { useThemeStore } from '@/store/themeStore';
import request from '@/utils/request';

const projectStore = useProjectStore();
const { locale, tl } = useAppI18n();

const loading = ref(false);
const uploading = ref(false);
const files = ref<FileAsset[]>([]);
const searchKeyword = ref('');
const uploadInputRef = ref<HTMLInputElement | null>(null);
const previewVisible = ref(false);
const previewUrl = ref('');
const previewTitle = ref('');
const settingsVisible = ref(false);
const savingSettings = ref(false);
const cleaning = ref(false);
const settingsForm = reactive<FileManagementSetting>({ auto_delete_on_unbind: false, auto_delete_zero_refs: false });
const referencesVisible = ref(false);
const referencesLoading = ref(false);
const referenceRows = ref<FileReferenceDetail[]>([]);
const currentReferenceFile = ref<FileAsset | null>(null);

const themeStore = useThemeStore();
const previewType = ref(''); // 'image' | 'pdf' | 'text' | 'other'
const previewContent = ref('');
const loadingContent = ref(false);
const previewMime = ref('');
const previewSize = ref('');
const previewLanguage = ref('plaintext');

const editorTheme = computed(() => (themeStore.isBlack ? 'vs-dark' : 'vs'));
const editorOptions = {
  minimap: { enabled: true },
  readOnly: true,
  scrollBeyondLastLine: false,
  fontSize: 14,
  tabSize: 4,
  renderLineHighlight: 'all',
  roundedSelection: false,
  occurrencesHighlight: 'off',
  padding: { top: 10, bottom: 10 },
  domReadOnly: true,
};

const copyBtnText = computed(() => isEnglish.value ? 'Copy' : '复制');

const formatBytes = (bytes: number) => {
  if (bytes === 0) return '0 B';
  const k = 1024;
  const sizes = ['B', 'KB', 'MB', 'GB'];
  const i = Math.floor(Math.log(bytes) / Math.log(k));
  return parseFloat((bytes / Math.pow(k, i)).toFixed(2)) + ' ' + sizes[i];
};

const getMonacoLanguage = (ext: string) => {
  const mapping: Record<string, string> = {
    '.py': 'python',
    '.js': 'javascript',
    '.ts': 'typescript',
    '.json': 'json',
    '.html': 'html',
    '.css': 'css',
    '.sql': 'sql',
    '.md': 'markdown',
    '.yaml': 'yaml',
    '.yml': 'yaml',
    '.xml': 'xml',
    '.sh': 'shell',
    '.bash': 'shell',
    '.bat': 'bat',
    '.ini': 'ini',
    '.conf': 'ini',
  };
  return mapping[ext.toLowerCase()] || 'plaintext';
};

const copyTextContent = async () => {
  try {
    await navigator.clipboard.writeText(previewContent.value);
    Message.success(isEnglish.value ? 'Copied to clipboard' : '已复制到剪贴板');
  } catch (err) {
    Message.error(isEnglish.value ? 'Copy failed' : '复制失败');
  }
};

const pagination = reactive({
  current: 1,
  pageSize: 20,
  total: 0,
  showTotal: true,
  showPageSize: true,
});

const isEnglish = computed(() => locale.value === 'en-US');
const pageTitle = computed(() => isEnglish.value ? 'File Management' : tl('文件管理'));
const pageSubtitle = computed(() => isEnglish.value ? 'Manage project attachments and reusable files' : tl('统一管理当前项目的附件和可复用文件'));
const searchPlaceholder = computed(() => isEnglish.value ? 'Search file name/type' : tl('搜索文件名/类型'));
const uploadText = computed(() => isEnglish.value ? 'Upload Files' : tl('上传文件'));
const refreshText = computed(() => isEnglish.value ? 'Refresh' : tl('刷新'));
const settingsText = computed(() => isEnglish.value ? 'Settings' : tl('设置'));
const noProjectText = computed(() => isEnglish.value ? 'Please select or create a project first.' : tl('请先选择或创建项目'));
const nameText = computed(() => isEnglish.value ? 'Name' : tl('文件名'));
const typeText = computed(() => isEnglish.value ? 'Type' : tl('类型'));
const sizeText = computed(() => isEnglish.value ? 'Size' : tl('大小'));
const statusText = computed(() => isEnglish.value ? 'Status' : tl('状态'));
const refsText = computed(() => isEnglish.value ? 'Refs' : tl('引用'));
const createdText = computed(() => isEnglish.value ? 'Created At' : tl('创建时间'));
const actionsText = computed(() => isEnglish.value ? 'Actions' : tl('操作'));
const previewText = computed(() => isEnglish.value ? 'Preview' : tl('预览'));
const downloadText = computed(() => isEnglish.value ? 'Download' : tl('下载'));
const deleteText = computed(() => isEnglish.value ? 'Delete' : tl('删除'));
const deleteConfirmText = computed(() => isEnglish.value ? 'Delete this file?' : tl('确认删除该文件？'));
const settingsTitle = computed(() => isEnglish.value ? 'File cleanup settings' : tl('文件清理设置'));
const settingsTipText = computed(() => isEnglish.value ? 'Automatic deletion is off by default. Enable it only when you want unused files to be removed automatically.' : tl('自动删除默认关闭。仅在你希望无引用文件被自动移除时开启。'));
const autoDeleteOnUnbindText = computed(() => isEnglish.value ? 'Delete when operation no longer uses the file' : tl('对应操作不再使用文件时自动删除'));
const autoDeleteOnUnbindDesc = computed(() => isEnglish.value ? 'When an interface/test step removes or replaces a file reference, delete the file if it has no other references.' : tl('接口/步骤移除或替换文件引用后，如果文件没有其它引用，则自动删除。'));
const autoDeleteZeroRefsText = computed(() => isEnglish.value ? 'Delete files with zero references' : tl('引用为 0 时自动删除'));
const autoDeleteZeroRefsDesc = computed(() => isEnglish.value ? 'Also clean project files that have no references when reference changes occur.' : tl('引用关系变化时，同时清理项目内引用数为 0 的文件。'));
const cleanupNowText = computed(() => isEnglish.value ? 'Clean now' : tl('立即清理引用为0文件'));
const cancelText = computed(() => isEnglish.value ? 'Cancel' : tl('取消'));
const saveText = computed(() => isEnglish.value ? 'Save' : tl('保存'));
const referencesTitle = computed(() => currentReferenceFile.value ? (isEnglish.value ? `References - ${currentReferenceFile.value.name || currentReferenceFile.value.original_name}` : `引用详情 - ${currentReferenceFile.value.name || currentReferenceFile.value.original_name}`) : (isEnglish.value ? 'References' : tl('引用详情')));
const noReferencesText = computed(() => isEnglish.value ? 'No references' : tl('暂无引用'));
const refTypeText = computed(() => isEnglish.value ? 'Type' : tl('类型'));
const refObjectText = computed(() => isEnglish.value ? 'Referenced by' : tl('引用对象'));
const refDescText = computed(() => isEnglish.value ? 'Description' : tl('说明'));
const createdByText = computed(() => isEnglish.value ? 'Created By' : tl('创建人'));


const unwrapObject = (res: any) => res?.data?.data || res?.data || res;

const shouldShowReferenceObjectId = (record: FileReferenceDetail) => {
  return record.ref_type !== 'llm_chat' && Boolean(record.object_id);
};

const formatReferenceObject = (record: FileReferenceDetail) => {
  const parts = [record.object_name || record.ref_id];
  const metaParts = [record.module_name, record.page_name, record.parent_name].filter(Boolean);
  if (metaParts.length) {
    parts.push(metaParts.join(' / '));
  }
  if (shouldShowReferenceObjectId(record)) {
    parts.push(`#${record.object_id}`);
  }
  return parts.filter(Boolean).join(' · ') || '-';
};

const openSettings = async () => {
  if (!projectStore.currentProjectId) return;
  settingsVisible.value = true;
  try {
    const payload = unwrapObject(await fileService.getSettings(projectStore.currentProjectId)) as FileManagementSetting;
    settingsForm.auto_delete_on_unbind = !!payload.auto_delete_on_unbind;
    settingsForm.auto_delete_zero_refs = !!payload.auto_delete_zero_refs;
  } catch (error: any) {
    Message.error(error?.message || (isEnglish.value ? 'Failed to load settings' : '加载设置失败'));
  }
};

const saveSettings = async () => {
  if (!projectStore.currentProjectId) return;
  savingSettings.value = true;
  try {
    await fileService.updateSettings(projectStore.currentProjectId, settingsForm);
    Message.success(isEnglish.value ? 'Saved' : '已保存');
    settingsVisible.value = false;
  } catch (error: any) {
    Message.error(error?.message || (isEnglish.value ? 'Save failed' : '保存失败'));
  } finally {
    savingSettings.value = false;
  }
};

const cleanupNow = async () => {
  if (!projectStore.currentProjectId) return;
  cleaning.value = true;
  try {
    const payload: any = unwrapObject(await fileService.cleanupUnreferenced(projectStore.currentProjectId));
    Message.success(isEnglish.value ? `Deleted ${payload.deleted_count || 0} file(s)` : `已清理 ${payload.deleted_count || 0} 个文件`);
    await loadFiles();
  } catch (error: any) {
    Message.error(error?.message || (isEnglish.value ? 'Cleanup failed' : '清理失败'));
  } finally {
    cleaning.value = false;
  }
};

const unwrapList = (res: any) => {
  const payload = res?.data?.data || res?.data || res;
  if (Array.isArray(payload)) return { list: payload, total: payload.length };
  if (Array.isArray(payload?.results)) return { list: payload.results, total: payload.count ?? payload.results.length };
  if (Array.isArray(payload?.data)) return { list: payload.data, total: payload.count ?? payload.data.length };
  if (Array.isArray(payload?.data?.results)) return { list: payload.data.results, total: payload.data.count ?? payload.data.results.length };
  return { list: [], total: 0 };
};


const openReferences = async (record: FileAsset) => {
  if (!projectStore.currentProjectId) return;
  currentReferenceFile.value = record;
  referencesVisible.value = true;
  referencesLoading.value = true;
  try {
    const payload: any = unwrapObject(await fileService.references(projectStore.currentProjectId, record.id));
    referenceRows.value = payload.results || payload.data || [];
  } catch (error: any) {
    Message.error(error?.message || (isEnglish.value ? 'Failed to load references' : '加载引用详情失败'));
    referenceRows.value = [];
  } finally {
    referencesLoading.value = false;
  }
};

const loadFiles = async () => {
  if (!projectStore.currentProjectId) {
    files.value = [];
    pagination.total = 0;
    return;
  }
  loading.value = true;
  try {
    const res = await fileService.list(projectStore.currentProjectId, {
      page: pagination.current,
      page_size: pagination.pageSize,
      search: searchKeyword.value || undefined,
      ordering: '-created_at',
    });
    const { list, total } = unwrapList(res);
    files.value = list;
    pagination.total = total;
  } catch (error: any) {
    Message.error(error?.message || (isEnglish.value ? 'Failed to load files' : '加载文件失败'));
  } finally {
    loading.value = false;
  }
};

const handlePageChange = (page: number) => {
  pagination.current = page;
  loadFiles();
};

const handlePageSizeChange = (pageSize: number) => {
  pagination.pageSize = pageSize;
  pagination.current = 1;
  loadFiles();
};

const handleUploadChange = async (event: Event) => {
  const input = event.target as HTMLInputElement;
  const selected = input.files ? Array.from(input.files) : [];
  if (!selected.length || !projectStore.currentProjectId) return;
  uploading.value = true;
  try {
    await fileService.upload(projectStore.currentProjectId, selected);
    Message.success(isEnglish.value ? `Uploaded ${selected.length} file(s)` : `已上传 ${selected.length} 个文件`);
    pagination.current = 1;
    await loadFiles();
  } catch (error: any) {
    Message.error(error?.message || (isEnglish.value ? 'Upload failed' : '上传失败'));
  } finally {
    uploading.value = false;
    input.value = '';
  }
};

const previewFile = async (record: FileAsset) => {
  if (!projectStore.currentProjectId) return;
  previewTitle.value = record.name || record.original_name;
  const token = localStorage.getItem('auth-accessToken') || '';

  const ext = (record.extension || '').toLowerCase();
  const mime = record.mime_type || '';
  previewMime.value = formatExtension(record.extension, record.mime_type);
  previewSize.value = formatBytes(record.size);
  previewLanguage.value = getMonacoLanguage(ext);

  if (mime.startsWith('image/') && mime !== 'image/svg+xml') {
    previewType.value = 'image';
    previewUrl.value = `/api/projects/${projectStore.currentProjectId}/files/${record.id}/preview/?token=${encodeURIComponent(token)}`;
    previewVisible.value = true;
  } else if (mime === 'application/pdf') {
    previewType.value = 'pdf';
    previewUrl.value = `/api/projects/${projectStore.currentProjectId}/files/${record.id}/preview/?token=${encodeURIComponent(token)}`;
    previewVisible.value = true;
  } else {
    // Treat as text / code preview
    previewType.value = 'text';
    previewContent.value = '';
    previewVisible.value = true;
    loadingContent.value = true;
    try {
      const response = await request.get(`/projects/${projectStore.currentProjectId}/files/${record.id}/preview/`, {
        params: { token },
        responseType: 'text'
      });
      const payload = unwrapObject(response);
      previewContent.value = typeof payload === 'string' ? payload : JSON.stringify(payload, null, 2);
    } catch (err: any) {
      previewContent.value = `[加载预览失败: ${err?.message || err}]`;
    } finally {
      loadingContent.value = false;
    }
  }
};

const downloadFile = (record: FileAsset) => {
  const token = localStorage.getItem('auth-accessToken') || '';
  window.open(`/api/projects/${projectStore.currentProjectId}/files/${record.id}/download/?token=${encodeURIComponent(token)}`, '_blank');
};

const deleteFile = async (record: FileAsset) => {
  if (!projectStore.currentProjectId) return;
  try {
    await fileService.delete(projectStore.currentProjectId, record.id);
    Message.success(isEnglish.value ? 'Deleted' : '已删除');
    await loadFiles();
  } catch (error: any) {
    Message.error(error?.message || (isEnglish.value ? 'Delete failed' : '删除失败'));
  }
};

const formatSize = (size: number) => {
  if (!size) return '0 B';
  if (size < 1024) return `${size} B`;
  if (size < 1024 * 1024) return `${(size / 1024).toFixed(1)} KB`;
  if (size < 1024 * 1024 * 1024) return `${(size / 1024 / 1024).toFixed(1)} MB`;
  return `${(size / 1024 / 1024 / 1024).toFixed(1)} GB`;
};

const formatExtension = (ext: string, mime: string) => {
  if (ext) {
    return ext.replace(/^\./, '').toUpperCase();
  }
  if (mime) {
    const parts = mime.split('/');
    return parts[parts.length - 1].toUpperCase();
  }
  return 'UNKNOWN';
};

const formatDate = (value: string) => value ? new Date(value).toLocaleString() : '-';

watch(() => projectStore.currentProjectId, () => {
  pagination.current = 1;
  loadFiles();
});

onMounted(loadFiles);
</script>

<style scoped>
.file-management-page { padding: 16px; }
.file-card { margin-top: 12px; }
.hidden-file-input { display: none; }
.mb-16 { margin-bottom: 16px; }
.preview-frame { width: 100%; height: 70vh; border: 0; border-radius: 4px; border: 1px solid var(--color-border-2); }
.setting-desc { margin-left: 12px; color: var(--color-text-3); font-size: 12px; }
.ref-object-one-line {
  max-width: 100%;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  font-weight: 500;
}

.preview-container {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  width: 100%;
}
.image-container {
  padding: 16px;
  background: var(--color-fill-1);
  border-radius: 8px;
  border: 1px solid var(--color-border-2);
}
.pdf-container {
  width: 100%;
}
.text-container {
  width: 100%;
}
.text-preview-shell {
  width: 100%;
  display: flex;
  flex-direction: column;
}
.text-preview-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 8px 16px;
  background: var(--color-fill-2);
  border: 1px solid var(--color-border-2);
  border-radius: 4px;
}
.editor-shell {
  box-shadow: 0 4px 12px rgba(0, 0, 0, 0.05);
}
</style>
