<template>
  <div class="runtime-log-page">
    <div v-if="!hasPermission" class="access-denied">
      <div class="denied-card">
        <h2>无访问权限</h2>
        <p>运行日志仅系统管理员或拥有操作日志查看权限的用户可访问。</p>
        <a-button type="primary" @click="$router.push('/dashboard')">返回首页</a-button>
      </div>
    </div>

    <template v-else>
      <div class="toolbar">
        <a-space wrap>
          <a-select v-model="selectedFile" style="width: 240px" placeholder="日志文件" @change="loadLogs">
            <a-option v-for="item in files" :key="item.name" :value="item.name">
              {{ item.name }} ({{ formatSize(item.size) }})
            </a-option>
          </a-select>
          <a-select v-model="level" style="width: 140px" allow-clear placeholder="级别" @change="loadLogs">
            <a-option value="INFO">INFO</a-option>
            <a-option value="WARNING">WARNING</a-option>
            <a-option value="ERROR">ERROR</a-option>
            <a-option value="DEBUG">DEBUG</a-option>
          </a-select>
          <a-select v-model="lineCount" style="width: 120px" @change="loadLogs">
            <a-option :value="100">100 行</a-option>
            <a-option :value="200">200 行</a-option>
            <a-option :value="500">500 行</a-option>
            <a-option :value="1000">1000 行</a-option>
            <a-option :value="2000">2000 行</a-option>
          </a-select>
          <a-button type="primary" :loading="loading" @click="loadLogs">刷新</a-button>
          <a-button :loading="downloading" @click="handleDownload">下载</a-button>
          <a-checkbox v-model="autoRefresh">自动刷新</a-checkbox>
        </a-space>
        <div class="meta" v-if="metaText">{{ metaText }}</div>
      </div>

      <div class="log-panel">
        <pre ref="logBoxRef" class="log-box">{{ logText }}</pre>
      </div>
    </template>
  </div>
</template>

<script setup lang="ts">
import { computed, nextTick, onMounted, onUnmounted, ref, watch } from 'vue';
import { Message } from '@arco-design/web-vue';
import { useAuthStore } from '@/store/authStore';
import {
  downloadRuntimeLogFile,
  fetchRuntimeLogFiles,
  fetchRuntimeLogTail,
  type RuntimeLogFileInfo,
} from '@/services/systemRuntimeLogService';

const authStore = useAuthStore();
const hasPermission = computed(() => {
  return (
    authStore.user?.is_staff ||
    authStore.hasPermission('accounts.view_operationlog') ||
    authStore.hasPermission('operation_logs.view_operationlog')
  );
});

const loading = ref(false);
const downloading = ref(false);
const autoRefresh = ref(false);
const selectedFile = ref('app.log');
const level = ref<string | undefined>(undefined);
const lineCount = ref(200);
const files = ref<RuntimeLogFileInfo[]>([]);
const lines = ref<string[]>([]);
const modifiedAt = ref<number | null>(null);
const fileSize = ref<number | null>(null);
const logBoxRef = ref<HTMLElement | null>(null);
let timer: number | null = null;

const logText = computed(() => (lines.value.length ? lines.value.join('\n') : '暂无日志内容'));
const metaText = computed(() => {
  if (!modifiedAt.value && fileSize.value == null) return '';
  const time = modifiedAt.value ? new Date(modifiedAt.value * 1000).toLocaleString() : '-';
  return `文件 ${selectedFile.value} · ${formatSize(fileSize.value || 0)} · 更新于 ${time} · ${lines.value.length} 行`;
});

function formatSize(size: number) {
  if (size < 1024) return `${size} B`;
  if (size < 1024 * 1024) return `${(size / 1024).toFixed(1)} KB`;
  return `${(size / 1024 / 1024).toFixed(1)} MB`;
}

async function loadFiles() {
  const res = await fetchRuntimeLogFiles();
  if (!res.success) {
    Message.error(res.error || '加载日志文件列表失败');
    return;
  }
  const payload = res.data as any;
  files.value = Array.isArray(payload) ? payload : payload?.results || [];
  if (!files.value.find((f) => f.name === selectedFile.value) && files.value[0]) {
    selectedFile.value = files.value[0].name;
  }
}

async function loadLogs() {
  if (!hasPermission.value) return;
  loading.value = true;
  try {
    await loadFiles();
    const res = await fetchRuntimeLogTail({
      file: selectedFile.value,
      lines: lineCount.value,
      level: level.value,
    });
    if (!res.success || !res.data) {
      Message.error(res.error || '读取运行日志失败');
      lines.value = [];
      return;
    }
    lines.value = res.data.lines || [];
    modifiedAt.value = res.data.modified_at;
    fileSize.value = res.data.size;
    await nextTick();
    if (logBoxRef.value) {
      logBoxRef.value.scrollTop = logBoxRef.value.scrollHeight;
    }
  } finally {
    loading.value = false;
  }
}

async function handleDownload() {
  downloading.value = true;
  try {
    await downloadRuntimeLogFile(selectedFile.value);
  } catch (e: any) {
    Message.error(e?.message || '下载失败');
  } finally {
    downloading.value = false;
  }
}

watch(autoRefresh, (enabled) => {
  if (timer) {
    window.clearInterval(timer);
    timer = null;
  }
  if (enabled) {
    timer = window.setInterval(() => {
      loadLogs();
    }, 5000);
  }
});

onMounted(() => {
  if (hasPermission.value) loadLogs();
});

onUnmounted(() => {
  if (timer) window.clearInterval(timer);
});
</script>

<style scoped>
.runtime-log-page {
  padding: 16px 20px;
  height: calc(100vh - 90px);
  display: flex;
  flex-direction: column;
  gap: 12px;
}
.access-denied {
  display: flex;
  justify-content: center;
  padding-top: 80px;
}
.denied-card {
  background: #fff;
  border-radius: 8px;
  padding: 32px 40px;
  text-align: center;
  box-shadow: 0 1px 4px rgba(0, 0, 0, 0.06);
}
.toolbar {
  display: flex;
  flex-direction: column;
  gap: 8px;
}
.meta {
  color: #86909c;
  font-size: 13px;
}
.log-panel {
  flex: 1;
  min-height: 0;
  background: #1d2129;
  border-radius: 8px;
  overflow: hidden;
}
.log-box {
  margin: 0;
  height: 100%;
  overflow: auto;
  padding: 14px 16px;
  color: #e5e6eb;
  font-size: 12px;
  line-height: 1.55;
  font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace;
  white-space: pre-wrap;
  word-break: break-word;
}
</style>
