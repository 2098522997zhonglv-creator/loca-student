<template>
  <div v-if="visible" class="task-stage-bar" :class="statusClass">
    <div class="task-stage-bar__head">
      <span class="task-stage-bar__label">{{ statusLabel }}</span>
      <span v-if="currentStage" class="task-stage-bar__current">{{ currentStage }}</span>
    </div>
    <ol v-if="stages.length" class="task-stage-bar__list">
      <li
        v-for="(s, idx) in stages"
        :key="s.stage_id || idx"
        class="task-stage-bar__item"
        :class="{
          'is-current': s.stage_id === currentStage,
          'is-done': isDone(s.stage_id),
        }"
      >
        <span class="dot" />
        <span class="name">{{ s.stage_id }}</span>
      </li>
    </ol>
    <p v-if="blockReason" class="task-stage-bar__block">{{ blockReason }}</p>
  </div>
</template>

<script setup lang="ts">
import { computed } from 'vue';

export interface StageItem {
  stage_id: string;
  goal?: string;
  acceptance?: string;
}

const props = defineProps<{
  taskStatus?: string;
  currentStage?: string;
  stagePlan?: StageItem[];
  acceptance?: Record<string, unknown> | null;
}>();

const stages = computed(() => props.stagePlan || []);

const visible = computed(
  () =>
    Boolean(props.taskStatus) ||
    Boolean(props.currentStage) ||
    stages.value.length > 1
);

const statusClass = computed(() => {
  const s = props.taskStatus || '';
  if (s === 'completed' || s === 'task_completed') return 'is-ok';
  if (s === 'blocked' || s === 'task_blocked') return 'is-blocked';
  if (s === 'cancelling' || s === 'cancelled') return 'is-cancel';
  return 'is-running';
});

const statusLabel = computed(() => {
  const s = props.taskStatus || '';
  if (s === 'completed' || s === 'task_completed') return '已验收';
  if (s === 'blocked' || s === 'task_blocked') return '未完全完成';
  if (s === 'cancelling') return '取消中';
  if (s === 'awaiting_approval') return '等待确认';
  return '执行中';
});

const blockReason = computed(() => {
  const reasons = props.acceptance?.reasons;
  if (Array.isArray(reasons) && reasons.length) {
    return reasons.join('；');
  }
  return '';
});

function isDone(stageId: string): boolean {
  const ids = stages.value.map((s) => s.stage_id);
  const cur = props.currentStage || '';
  const curIdx = ids.indexOf(cur);
  const idx = ids.indexOf(stageId);
  if (curIdx < 0 || idx < 0) return false;
  if (props.taskStatus === 'completed' || props.taskStatus === 'task_completed') {
    return true;
  }
  return idx < curIdx;
}
</script>

<style scoped>
.task-stage-bar {
  margin: 0 16px 8px;
  padding: 8px 12px;
  border-radius: 8px;
  border: 1px solid rgba(15, 23, 42, 0.08);
  background: linear-gradient(180deg, rgba(248, 250, 252, 0.95), rgba(241, 245, 249, 0.9));
  font-size: 12px;
  color: #334155;
}
.task-stage-bar.is-ok {
  border-color: rgba(16, 185, 129, 0.35);
  background: rgba(236, 253, 245, 0.9);
}
.task-stage-bar.is-blocked {
  border-color: rgba(245, 158, 11, 0.4);
  background: rgba(255, 251, 235, 0.95);
}
.task-stage-bar.is-cancel {
  border-color: rgba(148, 163, 184, 0.5);
}
.task-stage-bar__head {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 6px;
}
.task-stage-bar__label {
  font-weight: 600;
  letter-spacing: 0.02em;
}
.task-stage-bar__current {
  color: #64748b;
}
.task-stage-bar__list {
  display: flex;
  flex-wrap: wrap;
  gap: 8px 12px;
  list-style: none;
  margin: 0;
  padding: 0;
}
.task-stage-bar__item {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  color: #94a3b8;
}
.task-stage-bar__item .dot {
  width: 6px;
  height: 6px;
  border-radius: 50%;
  background: #cbd5e1;
}
.task-stage-bar__item.is-current {
  color: #0f172a;
  font-weight: 600;
}
.task-stage-bar__item.is-current .dot {
  background: #2563eb;
  box-shadow: 0 0 0 3px rgba(37, 99, 235, 0.18);
}
.task-stage-bar__item.is-done {
  color: #059669;
}
.task-stage-bar__item.is-done .dot {
  background: #10b981;
}
.task-stage-bar__block {
  margin: 6px 0 0;
  color: #b45309;
  line-height: 1.4;
}
</style>
