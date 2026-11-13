<template>
  <a-modal
    :visible="visible"
    :title="title"
    :footer="false"
    width="720px"
    unmount-on-close
    @update:visible="emit('update:visible', $event)"
  >
    <div class="metrics-body">
      <div class="metrics-toolbar">
        <a-button size="small" :loading="loading" @click="reload">刷新</a-button>
        <span v-if="updatedAt" class="metrics-updated">更新于 {{ updatedAt }}</span>
        <span v-if="deployLabel" class="metrics-updated">{{ deployLabel }}</span>
      </div>

      <a-spin :loading="loading" style="width: 100%">
        <div v-if="error" class="metrics-error">{{ error }}</div>
        <template v-else-if="metrics">
          <div class="metrics-grid">
            <div class="metric-card">
              <div class="metric-label">采样任务</div>
              <div class="metric-value">{{ metrics.tasks_sampled ?? 0 }}</div>
            </div>
            <div class="metric-card">
              <div class="metric-label">完成率</div>
              <div class="metric-value">{{ pct(metrics.completion_rate) }}</div>
            </div>
            <div class="metric-card">
              <div class="metric-label">阻塞率</div>
              <div class="metric-value">{{ pct(metrics.block_rate) }}</div>
            </div>
            <div class="metric-card">
              <div class="metric-label">误报完成</div>
              <div class="metric-value warn">{{ metrics.false_complete_count ?? 0 }}</div>
            </div>
            <div class="metric-card">
              <div class="metric-label">阶段推进事件</div>
              <div class="metric-value">{{ metrics.stage_advance_events ?? 0 }}</div>
            </div>
            <div class="metric-card">
              <div class="metric-label">工具事件</div>
              <div class="metric-value">{{ telemetryTotal }}</div>
            </div>
          </div>

          <div class="metrics-section">
            <h4>任务状态分布</h4>
            <div class="chip-row">
              <span
                v-for="(n, k) in statusCounts"
                :key="k"
                class="chip"
              >{{ k }}: {{ n }}</span>
              <span v-if="!Object.keys(statusCounts).length" class="muted">暂无</span>
            </div>
          </div>

          <div class="metrics-section">
            <h4>治理 KPI</h4>
            <div class="chip-row">
              <span
                v-for="(n, k) in governance"
                :key="k"
                class="chip"
              >{{ k }}: {{ formatVal(n) }}</span>
              <span v-if="!Object.keys(governance).length" class="muted">暂无</span>
            </div>
          </div>

          <div class="metrics-section">
            <h4>错误类型 Top</h4>
            <div class="chip-row">
              <span
                v-for="(n, k) in errorKinds"
                :key="k"
                class="chip chip-err"
              >{{ k }}: {{ n }}</span>
              <span v-if="!Object.keys(errorKinds).length" class="muted">暂无</span>
            </div>
          </div>
        </template>
        <div v-else class="muted">暂无指标数据</div>
      </a-spin>
    </div>
  </a-modal>
</template>

<script setup lang="ts">
import { computed, ref, watch } from 'vue';
import { fetchAgentQualityMetrics } from '@/features/langgraph/services/chatService';

const props = defineProps<{ visible: boolean }>();
const emit = defineEmits<{ (e: 'update:visible', v: boolean): void }>();

const loading = ref(false);
const error = ref('');
const metrics = ref<Record<string, unknown> | null>(null);
const updatedAt = ref('');

const title = 'Agent 质量指标';

const statusCounts = computed(
  () => (metrics.value?.status_counts as Record<string, number>) || {}
);
const telemetry = computed(
  () => (metrics.value?.telemetry as Record<string, unknown>) || {}
);
const governance = computed(
  () => (telemetry.value.governance_kpis as Record<string, unknown>) || {}
);
const errorKinds = computed(
  () => (telemetry.value.error_kinds as Record<string, number>) || {}
);
const telemetryTotal = computed(() => Number(telemetry.value.total_tool_events || 0));
const deployLabel = computed(() => {
  const d = (metrics.value?.deploy as Record<string, unknown> | undefined) || {};
  const sha = String(d.git_sha || '');
  const schema = d.config_schema_version;
  if (!sha && schema == null) return '';
  return `部署 ${sha || '—'} · schema v${schema ?? '—'}`;
});

function pct(v: unknown): string {
  const n = Number(v);
  if (Number.isNaN(n)) return '-';
  return `${(n * 100).toFixed(1)}%`;
}

function formatVal(v: unknown): string {
  if (typeof v === 'number') {
    return Number.isInteger(v) ? String(v) : v.toFixed(4);
  }
  if (v && typeof v === 'object') return JSON.stringify(v);
  return String(v ?? '-');
}

async function reload() {
  loading.value = true;
  error.value = '';
  try {
    const result = await fetchAgentQualityMetrics({ scope: 'user', limit: 200 });
    metrics.value = result.data;
    updatedAt.value = new Date().toLocaleTimeString();
    if (!result.data) {
      error.value =
        result.error || '未能获取指标（可能未登录或服务不可用）';
    }
  } catch (e) {
    error.value = e instanceof Error ? e.message : '加载失败';
  } finally {
    loading.value = false;
  }
}

watch(
  () => props.visible,
  (v) => {
    if (v) reload();
  }
);
</script>

<style scoped>
.metrics-body {
  min-height: 220px;
}
.metrics-toolbar {
  display: flex;
  align-items: center;
  gap: 12px;
  margin-bottom: 12px;
}
.metrics-updated {
  color: #94a3b8;
  font-size: 12px;
}
.metrics-error {
  color: #b45309;
  margin-bottom: 8px;
}
.metrics-grid {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 10px;
  margin-bottom: 16px;
}
.metric-card {
  border: 1px solid rgba(15, 23, 42, 0.08);
  border-radius: 10px;
  padding: 12px;
  background: linear-gradient(180deg, #f8fafc, #f1f5f9);
}
.metric-label {
  font-size: 12px;
  color: #64748b;
  margin-bottom: 6px;
}
.metric-value {
  font-size: 22px;
  font-weight: 700;
  color: #0f172a;
}
.metric-value.warn {
  color: #b45309;
}
.metrics-section {
  margin-top: 14px;
}
.metrics-section h4 {
  margin: 0 0 8px;
  font-size: 13px;
  color: #334155;
}
.chip-row {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
}
.chip {
  font-size: 12px;
  padding: 3px 8px;
  border-radius: 999px;
  background: rgba(37, 99, 235, 0.08);
  color: #1e3a8a;
}
.chip-err {
  background: rgba(245, 158, 11, 0.14);
  color: #92400e;
}
.muted {
  color: #94a3b8;
  font-size: 12px;
}
@media (max-width: 640px) {
  .metrics-grid {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }
}
</style>
