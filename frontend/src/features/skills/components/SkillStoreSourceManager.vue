<template>
  <a-modal
    v-model:visible="visible"
    :title="text.title"
    :width="640"
    :footer="false"
    :unmount-on-close="true"
    @cancel="handleCancel"
  >
    <div class="source-manager">
      <!-- 默认源（只读展示） -->
      <div class="section-title">{{ text.defaultSourceTitle }}</div>
      <div class="default-source" v-if="defaultSource">
        <div class="source-name">
          <icon-star-fill style="color: rgb(var(--gold-6))" />
          {{ defaultSource.name }}
          <a-tag size="small" color="gold">{{ text.builtinTag }}</a-tag>
        </div>
        <div class="source-url">{{ defaultSource.baseUrl }}</div>
        <div class="source-hint">{{ text.defaultSourceHint }}</div>
      </div>
      <div v-else class="source-hint">{{ text.noDefaultSource }}</div>

      <a-divider />

      <!-- 自定义源 -->
      <div v-if="allowCustomSource">
        <div class="section-header">
          <span class="section-title">{{ text.customSourcesTitle }}</span>
          <a-button type="primary" size="small" @click="openAddForm">
            <template #icon><icon-plus /></template>
            {{ text.addSource }}
          </a-button>
        </div>

        <a-form
          v-if="formVisible"
          :model="formData"
          layout="vertical"
          class="source-form"
        >
          <a-form-item :label="text.fieldName" required>
            <a-input
              v-model="formData.name"
              :placeholder="text.fieldNamePlaceholder"
              :max-length="40"
            />
          </a-form-item>
          <a-form-item :label="text.fieldBaseUrl" required>
            <a-input
              v-model="formData.baseUrl"
              :placeholder="text.fieldBaseUrlPlaceholder"
            />
            <template #extra>
              <span class="form-tip">{{ text.fieldBaseUrlTip }}</span>
            </template>
          </a-form-item>
          <a-space>
            <a-button type="primary" @click="saveForm">{{ text.save }}</a-button>
            <a-button @click="cancelForm">{{ text.cancel }}</a-button>
          </a-space>
        </a-form>

        <div v-if="customSources.length === 0 && !formVisible" class="empty-custom">
          {{ text.noCustomSources }}
        </div>

        <div class="custom-list">
          <div
            v-for="source in customSources"
            :key="source.id"
            class="custom-item"
          >
            <div class="source-info">
              <div class="source-name">{{ source.name }}</div>
              <div class="source-url">{{ source.baseUrl }}</div>
            </div>
            <a-space>
              <a-button type="text" size="mini" @click="openEditForm(source)">
                <icon-edit />
              </a-button>
              <a-popconfirm
                :content="text.deleteConfirm"
                @ok="removeSource(source.id)"
                :ok-text="text.ok"
                :cancel-text="text.cancel"
              >
                <a-button type="text" size="mini" status="danger">
                  <icon-delete />
                </a-button>
              </a-popconfirm>
            </a-space>
          </div>
        </div>
      </div>
      <div v-else class="forbidden-hint">
        <icon-info-circle />
        {{ text.customDisabled }}
      </div>
    </div>
  </a-modal>
</template>

<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { Message } from '@arco-design/web-vue'
import { SkillStoreService } from '../services/skillStoreService'
import type { SkillStoreSource } from '../types'
import { useAppI18n } from '@/composables/useAppI18n'

const props = defineProps<{
  visible: boolean
  defaultSource: SkillStoreSource | null
  allowCustomSource: boolean
}>()

const emit = defineEmits<{
  (e: 'update:visible', value: boolean): void
  (e: 'sources-updated'): void
}>()

const { isEnglish } = useAppI18n()

const text = computed(() => (
  isEnglish.value
    ? {
        title: 'Manage Skill Hub Sources',
        defaultSourceTitle: 'Default source',
        builtinTag: 'Built-in',
        defaultSourceHint: 'Configured by the platform. Cannot be modified here.',
        noDefaultSource: 'No default source configured by the platform.',
        customSourcesTitle: 'Custom sources',
        addSource: 'Add source',
        fieldName: 'Display name',
        fieldNamePlaceholder: 'e.g. My Internal Store',
        fieldBaseUrl: 'Base URL',
        fieldBaseUrlPlaceholder: 'https://example.com/path/to/store/',
        fieldBaseUrlTip: 'Must be HTTPS. Should be the directory containing manifest.json.',
        save: 'Save',
        cancel: 'Cancel',
        ok: 'OK',
        noCustomSources: 'No custom sources yet.',
        deleteConfirm: 'Remove this custom source?',
        customDisabled: 'Custom sources are disabled by the platform.',
        nameRequired: 'Display name is required',
        urlRequired: 'Base URL is required',
        urlMustHttps: 'Base URL must use HTTPS',
        saveSuccess: 'Source saved',
        deleteSuccess: 'Source removed',
      }
    : {
        title: '管理 Skill 中心源',
        defaultSourceTitle: '默认源',
        builtinTag: '内置',
        defaultSourceHint: '由平台部署方配置，此处不可修改。',
        noDefaultSource: '平台未配置默认源。',
        customSourcesTitle: '自定义源',
        addSource: '添加源',
        fieldName: '显示名称',
        fieldNamePlaceholder: '例如：公司内网 Skill 中心',
        fieldBaseUrl: '基础 URL',
        fieldBaseUrlPlaceholder: 'https://example.com/path/to/store/',
        fieldBaseUrlTip: '必须为 HTTPS，且应是包含 manifest.json 的目录。',
        save: '保存',
        cancel: '取消',
        ok: '确定',
        noCustomSources: '暂无自定义源。',
        deleteConfirm: '确定删除此自定义源？',
        customDisabled: '平台已禁用自定义源功能。',
        nameRequired: '请输入显示名称',
        urlRequired: '请输入基础 URL',
        urlMustHttps: '基础 URL 必须为 HTTPS',
        saveSuccess: '源已保存',
        deleteSuccess: '源已删除',
      }
))

const visible = computed({
  get: () => props.visible,
  set: (v: boolean) => emit('update:visible', v),
})

const customSources = ref<SkillStoreSource[]>([])
const formVisible = ref(false)
const editingId = ref<string | null>(null)
const formData = ref<{ name: string; baseUrl: string }>({ name: '', baseUrl: '' })

function refreshList() {
  customSources.value = SkillStoreService.getCustomSources()
}

function openAddForm() {
  editingId.value = null
  formData.value = { name: '', baseUrl: '' }
  formVisible.value = true
}

function openEditForm(source: SkillStoreSource) {
  editingId.value = source.id
  formData.value = { name: source.name, baseUrl: source.baseUrl }
  formVisible.value = true
}

function cancelForm() {
  formVisible.value = false
  editingId.value = null
}

function saveForm() {
  const name = formData.value.name.trim()
  const baseUrl = formData.value.baseUrl.trim()
  if (!name) {
    Message.warning(text.value.nameRequired)
    return
  }
  if (!baseUrl) {
    Message.warning(text.value.urlRequired)
    return
  }
  if (!/^https:\/\//i.test(baseUrl)) {
    Message.warning(text.value.urlMustHttps)
    return
  }

  if (editingId.value) {
    SkillStoreService.updateCustomSource(editingId.value, { name, baseUrl })
  } else {
    SkillStoreService.addCustomSource(name, baseUrl)
  }
  refreshList()
  emit('sources-updated')
  formVisible.value = false
  editingId.value = null
  Message.success(text.value.saveSuccess)
}

function removeSource(id: string) {
  SkillStoreService.removeCustomSource(id)
  refreshList()
  emit('sources-updated')
  Message.success(text.value.deleteSuccess)
}

function handleCancel() {
  visible.value = false
  formVisible.value = false
  editingId.value = null
}

watch(
  () => props.visible,
  (v) => {
    if (v) refreshList()
  },
  { immediate: true }
)
</script>

<style scoped>
.source-manager {
  padding: 4px 0;
}

.section-title {
  font-weight: 600;
  font-size: 14px;
  margin-bottom: 8px;
}

.section-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 12px;
}

.default-source {
  background: var(--color-fill-2);
  border-radius: 6px;
  padding: 12px;
}

.source-name {
  display: flex;
  align-items: center;
  gap: 6px;
  font-weight: 500;
  margin-bottom: 4px;
}

.source-url {
  font-size: 12px;
  color: var(--color-text-3);
  word-break: break-all;
  font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
}

.source-hint {
  font-size: 12px;
  color: var(--color-text-3);
  margin-top: 6px;
}

.source-form {
  background: var(--color-fill-1);
  border-radius: 6px;
  padding: 16px;
  margin-bottom: 12px;
}

.form-tip {
  color: var(--color-text-3);
  font-size: 12px;
}

.custom-list {
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.custom-item {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 10px 12px;
  background: var(--color-bg-2);
  border: 1px solid var(--color-border-2);
  border-radius: 6px;
  gap: 12px;
}

.source-info {
  flex: 1;
  min-width: 0;
}

.empty-custom {
  text-align: center;
  color: var(--color-text-3);
  font-size: 13px;
  padding: 16px 0;
}

.forbidden-hint {
  display: flex;
  align-items: center;
  gap: 6px;
  color: var(--color-text-3);
  font-size: 13px;
  padding: 12px 0;
}
</style>
