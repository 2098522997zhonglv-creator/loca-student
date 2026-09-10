<template>
  <div class="file-attachment-picker">
    <div v-if="selectedFiles.length" class="file-list">
      <a-tag
        v-for="file in selectedFiles"
        :key="file.id"
        closable
        color="blue"
        @close="removeFile(file.id)"
      >
        {{ file.name || file.original_name }} ({{ formatSize(file.size) }})
      </a-tag>
    </div>
    <input ref="inputRef" type="file" multiple class="hidden-file-input" @change="handleFileChange" />
    <a-space>
      <a-button size="small" type="outline" :loading="uploading" @click="inputRef?.click()">
        <template #icon><icon-upload /></template>
        {{ buttonText }}
      </a-button>
      <a-button v-if="modelValue.length" size="small" type="text" @click="clearFiles">清空附件</a-button>
    </a-space>
  </div>
</template>

<script setup lang="ts">
import { computed, ref, watch } from 'vue';
import { Message } from '@arco-design/web-vue';
import { IconUpload } from '@arco-design/web-vue/es/icon';
import { fileService } from '../services/fileService';
import type { FileAsset } from '../types';

const props = withDefaults(defineProps<{
  modelValue: number[];
  projectId: number | string;
  buttonText?: string;
}>(), {
  buttonText: '上传附件'
});

const emit = defineEmits<{
  'update:modelValue': [value: number[]];
  'update:files': [value: FileAsset[]];
}>();

const inputRef = ref<HTMLInputElement | null>(null);
const uploading = ref(false);
const selectedFiles = ref<FileAsset[]>([]);
const idsKey = computed(() => (props.modelValue || []).join(','));

const formatSize = (size: number) => {
  if (!size) return '0B';
  if (size < 1024) return `${size}B`;
  if (size < 1024 * 1024) return `${(size / 1024).toFixed(1)}KB`;
  return `${(size / 1024 / 1024).toFixed(1)}MB`;
};

const loadSelected = async () => {
  if (!props.projectId || !props.modelValue?.length) {
    selectedFiles.value = [];
    emit('update:files', []);
    return;
  }
  try {
    const res: any = await fileService.validate(props.projectId, props.modelValue);
    const payload = res?.data?.data || res?.data || res;
    selectedFiles.value = payload.files || [];
    emit('update:files', selectedFiles.value);
  } catch {
    selectedFiles.value = [];
  }
};

watch(() => [props.projectId, idsKey.value], loadSelected, { immediate: true });

const handleFileChange = async (event: Event) => {
  const input = event.target as HTMLInputElement;
  const files = input.files ? Array.from(input.files) : [];
  if (!files.length) return;
  uploading.value = true;
  try {
    const res: any = await fileService.upload(props.projectId, files);
    const payload = res?.data?.data || res?.data || res;
    const uploaded: FileAsset[] = Array.isArray(payload) ? payload : [payload];
    const nextIds = Array.from(new Set([...(props.modelValue || []), ...uploaded.map((item) => item.file_id || item.id)]));
    selectedFiles.value = [...selectedFiles.value, ...uploaded];
    emit('update:modelValue', nextIds);
    emit('update:files', selectedFiles.value);
    Message.success(`已上传 ${uploaded.length} 个附件`);
  } catch (error: any) {
    Message.error(error?.error || '附件上传失败');
  } finally {
    uploading.value = false;
    input.value = '';
  }
};

const removeFile = (id: number) => {
  selectedFiles.value = selectedFiles.value.filter((item) => item.id !== id);
  emit('update:modelValue', (props.modelValue || []).filter((item) => item !== id));
  emit('update:files', selectedFiles.value);
};

const clearFiles = () => {
  selectedFiles.value = [];
  emit('update:modelValue', []);
  emit('update:files', []);
};
</script>

<style scoped>
.file-attachment-picker { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; }
.file-list { display: flex; gap: 6px; flex-wrap: wrap; }
.hidden-file-input { display: none; }
</style>
