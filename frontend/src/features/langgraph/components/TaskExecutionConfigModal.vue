<template>
  <a-modal
    :visible="visible"
    title="任务执行配置"
    :ok-text="taskId ? '保存到任务' : '应用'"
    :cancel-text="'取消'"
    width="560px"
    unmount-on-close
    :ok-loading="saving"
    @ok="apply"
    @cancel="emit('update:visible', false)"
    @update:visible="emit('update:visible', $event)"
  >
    <p class="hint">
      业务检查点与工具调用上限分开配置。提交后由服务端策略裁剪并回显有效值；留空则使用项目默认。
    </p>
    <p v-if="liveBudgetLabel" class="live">当前：{{ liveBudgetLabel }}</p>
    <a-form :model="form" layout="vertical" size="small">
      <a-row :gutter="12">
        <a-col :span="12">
          <a-form-item label="工具调用上限">
            <a-input-number
              v-model="form.max_tool_actions"
              :min="1"
              :max="200"
              placeholder="默认 30"
              style="width: 100%"
            />
          </a-form-item>
        </a-col>
        <a-col :span="12">
          <a-form-item label="无进展重复上限">
            <a-input-number
              v-model="form.max_no_progress_repeats"
              :min="1"
              :max="10"
              placeholder="默认 3"
              style="width: 100%"
            />
          </a-form-item>
        </a-col>
        <a-col :span="12">
          <a-form-item label="单步最大尝试">
            <a-input-number
              v-model="form.max_attempts_per_step"
              :min="1"
              :max="10"
              placeholder="默认 3"
              style="width: 100%"
            />
          </a-form-item>
        </a-col>
        <a-col :span="12">
          <a-form-item label="最长时长(秒)">
            <a-input-number
              v-model="form.max_duration_seconds"
              :min="30"
              :max="3600"
              placeholder="默认 900"
              style="width: 100%"
            />
          </a-form-item>
        </a-col>
        <a-col :span="24">
          <a-form-item label="允许 Skill（逗号分隔，与现有取交集）">
            <a-input
              v-model="form.allowed_skills"
              placeholder="如 playwright-skill,loca-stude"
              allow-clear
            />
          </a-form-item>
        </a-col>
        <a-col :span="12">
          <a-form-item label="允许动作">
            <a-input
              v-model="form.allow_actions"
              placeholder="upload,add_to_cart"
              allow-clear
            />
          </a-form-item>
        </a-col>
        <a-col :span="12">
          <a-form-item label="禁止动作（只增不减）">
            <a-input
              v-model="form.deny_actions"
              placeholder="submit_order,pay"
              allow-clear
            />
          </a-form-item>
        </a-col>
      </a-row>
    </a-form>

    <div class="preview-toolbar">
      <a-button size="small" :loading="previewing" @click="preview">预检有效配置</a-button>
      <span v-if="configVersion != null" class="ver">config_v{{ configVersion }}</span>
      <span v-if="previewError" class="err">{{ previewError }}</span>
    </div>
    <pre v-if="previewSummary" class="preview">{{ previewSummary }}</pre>
  </a-modal>
</template>

<script setup lang="ts">
import { computed, reactive, ref, watch } from 'vue';
import { Message } from '@arco-design/web-vue';
import {
  patchTaskExecutionConfig,
  validateTaskExecutionConfig,
} from '@/features/langgraph/services/chatService';

export type ExecutionConfigOverrides = {
  max_tool_actions?: number | null;
  max_no_progress_repeats?: number | null;
  max_attempts_per_step?: number | null;
  max_duration_seconds?: number | null;
  allowed_skills?: string | null;
  allow_actions?: string | null;
  deny_actions?: string | null;
};

const props = defineProps<{
  visible: boolean;
  modelValue?: ExecutionConfigOverrides | null;
  /** 已有任务时 PATCH 持久化（RC12） */
  taskId?: string | null;
  configVersion?: number | null;
  effectiveConfig?: {
    expected_business_steps?: number;
    completed_business_steps?: number;
    max_tool_actions?: number;
    used_tool_actions?: number;
    config_version?: number;
  } | null;
}>();

const emit = defineEmits<{
  (e: 'update:visible', v: boolean): void;
  (e: 'update:modelValue', v: ExecutionConfigOverrides | null): void;
  (e: 'saved', payload: { config_version?: number; budget?: Record<string, unknown> }): void;
}>();

const form = reactive<{
  max_tool_actions: number | undefined;
  max_no_progress_repeats: number | undefined;
  max_attempts_per_step: number | undefined;
  max_duration_seconds: number | undefined;
  allowed_skills: string | undefined;
  allow_actions: string | undefined;
  deny_actions: string | undefined;
}>({
  max_tool_actions: undefined,
  max_no_progress_repeats: undefined,
  max_attempts_per_step: undefined,
  max_duration_seconds: undefined,
  allowed_skills: undefined,
  allow_actions: undefined,
  deny_actions: undefined,
});

const previewing = ref(false);
const saving = ref(false);
const previewError = ref('');
const previewSummary = ref('');

const liveBudgetLabel = computed(() => {
  const cfg = props.effectiveConfig;
  if (!cfg) return '';
  const done = Number(cfg.completed_business_steps ?? 0);
  const exp = Number(cfg.expected_business_steps ?? 0);
  const used = Number(cfg.used_tool_actions ?? 0);
  const maxA = Number(cfg.max_tool_actions ?? 0);
  if (!exp && !maxA) return '';
  return `业务 ${done}/${exp || '—'} · 工具 ${used}/${maxA || '—'}`;
});

function syncFromProps() {
  const src = props.modelValue || {};
  const eff = props.effectiveConfig || {};
  form.max_tool_actions =
    src.max_tool_actions ??
    (eff.max_tool_actions != null ? Number(eff.max_tool_actions) : undefined);
  form.max_no_progress_repeats = src.max_no_progress_repeats ?? undefined;
  form.max_attempts_per_step = src.max_attempts_per_step ?? undefined;
  form.max_duration_seconds = src.max_duration_seconds ?? undefined;
  form.allowed_skills = src.allowed_skills ?? undefined;
  form.allow_actions = src.allow_actions ?? undefined;
  form.deny_actions = src.deny_actions ?? undefined;
}

watch(
  () => props.visible,
  (v) => {
    if (v) {
      syncFromProps();
      previewError.value = '';
      previewSummary.value = '';
    }
  }
);

function buildOverrides(): ExecutionConfigOverrides {
  const out: ExecutionConfigOverrides = {};
  if (form.max_tool_actions != null) out.max_tool_actions = form.max_tool_actions;
  if (form.max_no_progress_repeats != null) {
    out.max_no_progress_repeats = form.max_no_progress_repeats;
  }
  if (form.max_attempts_per_step != null) {
    out.max_attempts_per_step = form.max_attempts_per_step;
  }
  if (form.max_duration_seconds != null) {
    out.max_duration_seconds = form.max_duration_seconds;
  }
  if (form.allowed_skills?.trim()) out.allowed_skills = form.allowed_skills.trim();
  if (form.allow_actions?.trim()) out.allow_actions = form.allow_actions.trim();
  if (form.deny_actions?.trim()) out.deny_actions = form.deny_actions.trim();
  return out;
}

async function preview() {
  previewing.value = true;
  previewError.value = '';
  previewSummary.value = '';
  try {
    const result = await validateTaskExecutionConfig({
      overrides: buildOverrides(),
    });
    if (!result.ok) {
      previewError.value = result.error || '预检失败';
      return;
    }
    previewSummary.value =
      result.summary ||
      JSON.stringify(result.effective_config || {}, null, 2);
  } catch (e) {
    previewError.value = e instanceof Error ? e.message : '预检失败';
  } finally {
    previewing.value = false;
  }
}

async function apply() {
  const ov = buildOverrides();
  const empty = !Object.keys(ov).length;

  if (props.taskId) {
    const ver = Number(
      props.configVersion ?? props.effectiveConfig?.config_version ?? 1
    );
    saving.value = true;
    try {
      const result = await patchTaskExecutionConfig({
        task_id: String(props.taskId),
        expected_version: ver,
        overrides: ov,
        reason: 'ui_config_panel',
      });
      if (!result.ok) {
        if (result.conflict) {
          Message.error(
            `配置版本冲突（当前 v${result.current_version ?? '?'}），请刷新后重试`
          );
        } else {
          Message.error(result.error || '保存失败');
        }
        return;
      }
      emit('update:modelValue', empty ? null : ov);
      emit('saved', {
        config_version: result.config_version,
        budget: result.budget,
      });
      emit('update:visible', false);
      Message.success(result.summary || `已保存配置 v${result.config_version}`);
    } finally {
      saving.value = false;
    }
    return;
  }

  emit('update:modelValue', empty ? null : ov);
  emit('update:visible', false);
  Message.success(empty ? '已清除覆盖，使用默认配置' : '已应用任务执行配置');
}
</script>

<style scoped>
.hint {
  margin: 0 0 8px;
  color: #64748b;
  font-size: 12px;
}
.live {
  margin: 0 0 12px;
  color: #0f172a;
  font-size: 12px;
  font-weight: 500;
}
.preview-toolbar {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-top: 4px;
}
.ver {
  font-size: 11px;
  color: #94a3b8;
}
.err {
  color: #dc2626;
  font-size: 12px;
}
.preview {
  margin: 8px 0 0;
  padding: 8px 10px;
  background: #f8fafc;
  border-radius: 6px;
  font-size: 12px;
  white-space: pre-wrap;
  max-height: 160px;
  overflow: auto;
}
</style>
