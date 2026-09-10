<template>
  <a-modal
    :visible="visible"
    :title="isEdit ? text.editKnowledgeBase : text.createKnowledgeBase"
    :width="500"
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
      <a-form-item :label="text.knowledgeBaseName" field="name">
        <a-input
          v-model="formData.name"
          :placeholder="text.knowledgeBaseNamePlaceholder"
          :max-length="100"
        />
      </a-form-item>

      <a-form-item :label="text.description" field="description">
        <a-textarea
          v-model="formData.description"
          :placeholder="text.descriptionPlaceholder"
          :rows="3"
          :max-length="500"
        />
      </a-form-item>

      <a-form-item :label="text.project" field="project">
        <a-select
          v-model="formData.project"
          :placeholder="text.projectPlaceholder"
          :loading="projectStore.loading"
          :disabled="isEdit"
        >
          <a-option
            v-for="project in projects"
            :key="project.value"
            :value="project.value"
            :label="project.label"
          />
        </a-select>
      </a-form-item>

      <a-row :gutter="16">
        <a-col :span="12">
          <a-form-item :label="text.chunkSize" field="chunk_size">
            <a-input-number
              v-model="formData.chunk_size"
              :placeholder="text.chunkSize"
              :min="100"
              :max="4000"
              :step="100"
              style="width: 100%"
            />
          </a-form-item>
        </a-col>
        <a-col :span="12">
          <a-form-item :label="text.chunkOverlap" field="chunk_overlap">
            <a-input-number
              v-model="formData.chunk_overlap"
              :placeholder="text.chunkOverlap"
              :min="0"
              :max="500"
              :step="50"
              style="width: 100%"
            />
          </a-form-item>
        </a-col>
      </a-row>

      <a-form-item :label="text.status" field="is_active">
        <a-switch
          v-model="formData.is_active"
          :checked-text="text.enabled"
          :unchecked-text="text.disabled"
        />
      </a-form-item>
    </a-form>
  </a-modal>
</template>

<script setup lang="ts">
import { ref, reactive, computed, watch } from 'vue';
import { Message } from '@arco-design/web-vue';
import { useAppI18n } from '@/composables/useAppI18n';
import { useProjectStore } from '@/store/projectStore';
import { KnowledgeService } from '../services/knowledgeService';
import type {
  KnowledgeBase,
  CreateKnowledgeBaseRequest,
  UpdateKnowledgeBaseRequest,
} from '../types/knowledge';

interface Props {
  visible: boolean;
  knowledgeBase?: KnowledgeBase | null;
}

const props = defineProps<Props>();
const emit = defineEmits<{
  submit: [];
  cancel: [];
}>();

const projectStore = useProjectStore();
const { isEnglish } = useAppI18n();
const formRef = ref();
const loading = ref(false);

const isEdit = computed(() => !!props.knowledgeBase);
const text = computed(() => (
  isEnglish.value
    ? {
        editKnowledgeBase: 'Edit knowledge base',
        createKnowledgeBase: 'New knowledge base',
        knowledgeBaseName: 'Knowledge base name',
        knowledgeBaseNamePlaceholder: 'Enter knowledge base name',
        description: 'Description',
        descriptionPlaceholder: 'Enter description (optional)',
        project: 'Project',
        projectPlaceholder: 'Select project',
        chunkSize: 'Chunk size',
        chunkOverlap: 'Chunk overlap',
        status: 'Status',
        enabled: 'Enabled',
        disabled: 'Disabled',
        validateNameRequired: 'Please enter a knowledge base name',
        validateNameMin: 'Knowledge base name must be at least 2 characters',
        validateNameMax: 'Knowledge base name must be at most 200 characters',
        validateProjectRequired: 'Please select a project',
        validateChunkSizeRequired: 'Please enter chunk size',
        validateChunkSizeRange: 'Chunk size must be between 100 and 4000',
        validateChunkOverlapRequired: 'Please enter chunk overlap',
        validateChunkOverlapRange: 'Chunk overlap must be between 0 and 500',
        formInvalid: 'Please check the form fields',
        saveFailed: 'Failed to save knowledge base',
      }
    : {
        editKnowledgeBase: '编辑知识库',
        createKnowledgeBase: '新建知识库',
        knowledgeBaseName: '知识库名称',
        knowledgeBaseNamePlaceholder: '请输入知识库名称',
        description: '描述',
        descriptionPlaceholder: '请输入知识库描述（可选）',
        project: '所属项目',
        projectPlaceholder: '请选择所属项目',
        chunkSize: '分块大小',
        chunkOverlap: '分块重叠',
        status: '状态',
        enabled: '启用',
        disabled: '禁用',
        validateNameRequired: '请输入知识库名称',
        validateNameMin: '知识库名称至少2个字符',
        validateNameMax: '知识库名称不能超过200个字符',
        validateProjectRequired: '请选择所属项目',
        validateChunkSizeRequired: '请输入分块大小',
        validateChunkSizeRange: '分块大小必须在100-4000之间',
        validateChunkOverlapRequired: '请输入分块重叠',
        validateChunkOverlapRange: '分块重叠必须在0-500之间',
        formInvalid: '请检查表单填写是否正确',
        saveFailed: '保存知识库失败',
      }
));

// 表单数据（简化版，嵌入服务配置使用全局配置）
const formData = reactive<CreateKnowledgeBaseRequest>({
  name: '',
  description: '',
  project: 0,
  chunk_size: 1000,
  chunk_overlap: 200,
  is_active: true,
});

const projects = computed(() => projectStore.projectOptions);

const rules = computed(() => ({
  name: [
    { required: true, message: text.value.validateNameRequired },
    { minLength: 2, message: text.value.validateNameMin },
    { maxLength: 200, message: text.value.validateNameMax },
  ],
  project: [
    { required: true, message: text.value.validateProjectRequired },
  ],
  chunk_size: [
    { required: true, message: text.value.validateChunkSizeRequired },
    { type: 'number', min: 100, max: 4000, message: text.value.validateChunkSizeRange },
  ],
  chunk_overlap: [
    { required: true, message: text.value.validateChunkOverlapRequired },
    { type: 'number', min: 0, max: 500, message: text.value.validateChunkOverlapRange },
  ],
}));

watch(() => props.visible, async (visible) => {
  if (visible) {
    resetForm();

    if (projects.value.length === 0) {
      await projectStore.fetchProjects();
    }

    if (props.knowledgeBase) {
      Object.assign(formData, {
        name: props.knowledgeBase.name,
        description: props.knowledgeBase.description || '',
        project: typeof props.knowledgeBase.project === 'string' 
          ? Number(props.knowledgeBase.project) 
          : props.knowledgeBase.project,
        chunk_size: props.knowledgeBase.chunk_size,
        chunk_overlap: props.knowledgeBase.chunk_overlap,
        is_active: props.knowledgeBase.is_active,
      });
    } else {
      if (projectStore.currentProjectId) {
        formData.project = Number(projectStore.currentProjectId);
      }
    }
  }
});

watch(
  () => projects.value,
  (newProjects) => {
    if (props.visible && props.knowledgeBase && newProjects.length > 0) {
      const correctProjectId = typeof props.knowledgeBase.project === 'string' 
        ? Number(props.knowledgeBase.project) 
        : props.knowledgeBase.project;
      
      if (formData.project === 0 || formData.project !== correctProjectId) {
        formData.project = correctProjectId;
      }
    }
  },
  { immediate: true }
);

const resetForm = () => {
  Object.assign(formData, {
    name: '',
    description: '',
    project: 0,
    chunk_size: 1000,
    chunk_overlap: 200,
    is_active: true,
  });
  formRef.value?.clearValidate();
};

const handleSubmit = async () => {
  try {
    await formRef.value?.validate();
    loading.value = true;

    if (isEdit.value && props.knowledgeBase) {
      const updateData: UpdateKnowledgeBaseRequest = {
        name: formData.name,
        description: formData.description,
        project: formData.project,
        chunk_size: formData.chunk_size,
        chunk_overlap: formData.chunk_overlap,
        is_active: formData.is_active,
      };
      await KnowledgeService.updateKnowledgeBase(props.knowledgeBase.id, updateData);
    } else {
      const createData: CreateKnowledgeBaseRequest = {
        name: formData.name,
        description: formData.description,
        project: formData.project,
        chunk_size: formData.chunk_size,
        chunk_overlap: formData.chunk_overlap,
        is_active: formData.is_active,
      };
      await KnowledgeService.createKnowledgeBase(createData);
    }

    emit('submit');
  } catch (error: any) {
    console.error('保存知识库失败:', error);
    if (error && typeof error === 'object' && 'errorFields' in error) {
      Message.error(text.value.formInvalid);
    } else {
      Message.error(error?.message || text.value.saveFailed);
    }
  } finally {
    loading.value = false;
  }
};

const handleCancel = () => {
  emit('cancel');
};
</script>

<style scoped>
:deep(.arco-form-item-label) {
  font-weight: 500;
}

:deep(.arco-input-number) {
  width: 100%;
}
</style>
