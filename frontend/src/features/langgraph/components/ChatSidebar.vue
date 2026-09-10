<template>
  <div class="chat-sidebar">
    <div class="sidebar-header">
      <!-- 未选中任何对话时:新对话和全选在同一行,各占一半 -->
      <div v-if="selectedSessions.length === 0" class="header-row">
        <a-button type="primary" style="flex: 1; margin-right: 8px;" @click="$emit('create-new-chat')">
          <template #icon>
            <icon-plus />
          </template>
          {{ text.newChat }}
        </a-button>
        <a-button
          v-if="sessions.length > 0"
          style="flex: 1;"
          @click="toggleSelectAll"
        >
          <template #icon>
            <icon-check-circle />
          </template>
          {{ text.selectAll }}
        </a-button>
      </div>
      
      <!-- 选中对话后:新对话在第一行,全选/批量删除在第二行 -->
      <template v-else>
        <div class="header-row">
          <a-button type="primary" block style="width: 100%;" @click="$emit('create-new-chat')">
            <template #icon>
              <icon-plus />
            </template>
            {{ text.newChat }}
          </a-button>
        </div>
        <div class="header-row" style="margin-top: 12px;">
          <a-space>
            <a-button
              size="small"
              @click="toggleSelectAll"
            >
              <template #icon>
                <icon-close-circle />
              </template>
              {{ text.clearSelection }}
            </a-button>
            <a-button
              type="primary"
              status="danger"
              size="small"
              @click="handleBatchDelete"
            >
              <template #icon>
                <icon-delete />
              </template>
              {{ text.batchDelete(selectedSessions.length) }}
            </a-button>
          </a-space>
        </div>
      </template>
    </div>

    <div class="chat-history-list">
      <div v-if="sessions.length === 0" class="empty-history">
        {{ text.noHistory }}
      </div>
      <div
        v-for="session in sessions"
        :key="session.id"
        :class="['chat-history-item', session.id === currentSessionId ? 'active' : '']"
      >
        <a-checkbox 
          v-model="selectedSessions"
          :value="session.id"
          class="history-item-checkbox"
          @click.stop
        />
        <div class="history-item-content" @click="editingSessionId !== session.id && $emit('switch-session', session.id)">
          <div class="history-item-info">
            <template v-if="editingSessionId === session.id">
              <a-input
                v-model="editingTitle"
                size="mini"
                autofocus
                @press-enter="handleSaveRename(session.id)"
                @blur="handleSaveRename(session.id)"
                @keydown.esc="cancelRename"
                @click.stop
                style="width: 100%; margin-bottom: 4px;"
              />
            </template>
            <template v-else>
              <div class="history-item-title">{{ session.title || text.untitledChat }}</div>
            </template>
            <div class="history-item-time">{{ formatTime(session.lastTime) }}</div>
          </div>
        </div>
        <div v-if="selectedSessions.length === 0 && editingSessionId !== session.id" class="history-item-actions">
          <a-space :size="4">
            <a-button type="text" size="mini" @click.stop="startRename(session)">
              <template #icon>
                <icon-edit style="color: #4e5969;" />
              </template>
            </a-button>
            <a-button type="text" size="mini" @click.stop="$emit('delete-session', session.id)">
              <template #icon>
                <icon-delete style="color: #f53f3f;" />
              </template>
            </a-button>
          </a-space>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref, computed } from 'vue';
import { Button as AButton, Checkbox as ACheckbox, Input as AInput, Space as ASpace, Modal, Message } from '@arco-design/web-vue';
import { IconPlus, IconDelete, IconCheckCircle, IconCloseCircle, IconEdit } from '@arco-design/web-vue/es/icon';
import { useAppI18n } from '@/composables/useAppI18n';

interface ChatSession {
  id: string;
  title: string;
  lastTime: Date;
  messageCount: number;
}

interface Props {
  sessions: ChatSession[];
  currentSessionId: string;
  isLoading: boolean;
}

const props = defineProps<Props>();
const { isEnglish } = useAppI18n();

const text = computed(() => (
  isEnglish.value
    ? {
        newChat: 'New Chat',
        selectAll: 'Select All',
        clearSelection: 'Clear Selection',
        batchDelete: (count: number) => `Batch Delete (${count})`,
        noHistory: 'No chat history',
        untitledChat: 'Untitled Chat',
        selectBeforeDelete: 'Select chats to delete first',
        confirmBatchDelete: 'Confirm Batch Delete',
        confirmBatchDeleteContent: (count: number) => `Delete the selected ${count} chat(s)? This action cannot be undone.`,
        confirmDelete: 'Delete',
        cancel: 'Cancel',
        unknownTime: 'Unknown time',
        today: 'Today',
        yesterday: 'Yesterday',
      }
    : {
        newChat: '新对话',
        selectAll: '全选',
        clearSelection: '取消全选',
        batchDelete: (count: number) => `批量删除 (${count})`,
        noHistory: '暂无历史对话',
        untitledChat: '未命名对话',
        selectBeforeDelete: '请先选择要删除的对话',
        confirmBatchDelete: '确认批量删除',
        confirmBatchDeleteContent: (count: number) => `确定要删除选中的 ${count} 个对话吗？此操作不可恢复。`,
        confirmDelete: '确认删除',
        cancel: '取消',
        unknownTime: '时间未知',
        today: '今天',
        yesterday: '昨天',
      }
));

const emit = defineEmits<{
  'create-new-chat': [];
  'switch-session': [id: string];
  'delete-session': [id: string];
  'batch-delete-sessions': [sessionIds: string[]];
  'rename-session': [id: string, title: string];
}>();

// 选中的会话列表
const selectedSessions = ref<string[]>([]);

// 编辑会话标题相关状态
const editingSessionId = ref<string | null>(null);
const editingTitle = ref<string>('');

// 进入编辑模式
const startRename = (session: ChatSession) => {
  editingSessionId.value = session.id;
  editingTitle.value = session.title;
};

// 取消编辑
const cancelRename = () => {
  editingSessionId.value = null;
  editingTitle.value = '';
};

// 保存重命名
const handleSaveRename = (id: string) => {
  if (!editingSessionId.value) return;
  const newTitle = editingTitle.value.trim();
  if (newTitle && newTitle !== props.sessions.find(s => s.id === id)?.title) {
    emit('rename-session', id, newTitle);
  }
  editingSessionId.value = null;
  editingTitle.value = '';
};


// 计算是否全选
const isAllSelected = computed(() => {
  return props.sessions.length > 0 && selectedSessions.value.length === props.sessions.length;
});

// 全选/取消全选
const toggleSelectAll = () => {
  if (isAllSelected.value || selectedSessions.value.length > 0) {
    // 取消全选
    selectedSessions.value = [];
  } else {
    // 全选
    selectedSessions.value = props.sessions.map(session => session.id);
  }
};

// 批量删除处理
const handleBatchDelete = () => {
  if (selectedSessions.value.length === 0) {
    Message.warning(text.value.selectBeforeDelete);
    return;
  }

  Modal.confirm({
    title: text.value.confirmBatchDelete,
    content: text.value.confirmBatchDeleteContent(selectedSessions.value.length),
    okText: text.value.confirmDelete,
    cancelText: text.value.cancel,
    okButtonProps: {
      status: 'danger',
    },
    onOk: () => {
      emit('batch-delete-sessions', [...selectedSessions.value]);
      selectedSessions.value = [];
    },
  });
};

// 格式化时间显示
const formatTime = (date: Date) => {
  if (!date || !(date instanceof Date) || isNaN(date.getTime()) || date.getTime() === 0) {
    return text.value.unknownTime;
  }

  const now = new Date();
  const today = new Date(now.getFullYear(), now.getMonth(), now.getDate());
  const yesterday = new Date(today);
  yesterday.setDate(yesterday.getDate() - 1);

  if (date >= today) {
    return `${text.value.today} ${date.getHours().toString().padStart(2, '0')}:${date.getMinutes().toString().padStart(2, '0')}`;
  } else if (date >= yesterday) {
    return `${text.value.yesterday} ${date.getHours().toString().padStart(2, '0')}:${date.getMinutes().toString().padStart(2, '0')}`;
  } else {
    if (isEnglish.value) {
      return `${date.getMonth() + 1}/${date.getDate()} ${date.getHours().toString().padStart(2, '0')}:${date.getMinutes().toString().padStart(2, '0')}`;
    }
    return `${date.getMonth() + 1}月${date.getDate()}日 ${date.getHours().toString().padStart(2, '0')}:${date.getMinutes().toString().padStart(2, '0')}`;
  }
};
</script>

<style scoped>
.chat-sidebar {
  width: 280px;
  background-color: #ffffff;
  border-right: 1px solid #e5e6eb;
  display: flex;
  flex-direction: column;
}

.sidebar-header {
  padding: 16px;
  border-bottom: 1px solid #e5e6eb;
}

.header-row {
  display: flex;
  align-items: center;
  width: 100%;
}

.chat-history-list {
  flex: 1;
  overflow-y: auto;
  padding: 12px;
}

.empty-history {
  padding: 16px;
  color: #86909c;
  text-align: center;
}

.chat-history-item {
  padding: 12px;
  border-radius: 8px;
  margin-bottom: 8px;
  display: flex;
  justify-content: space-between;
  align-items: center;
  transition: background-color 0.2s;
}

.chat-history-item:hover {
  background-color: #f2f3f5;
}

.chat-history-item.active {
  background-color: #e8f3ff;
}

.history-item-checkbox {
  margin-right: 8px;
  flex-shrink: 0;
}

.history-item-content {
  display: flex;
  align-items: center;
  flex: 1;
  min-width: 0;
  cursor: pointer;
}

.history-item-icon {
  font-size: 16px;
  color: #4e5969;
  margin-right: 8px;
}

.history-item-info {
  flex: 1;
  min-width: 0;
}

.history-item-title {
  font-size: 14px;
  font-weight: 500;
  color: #1d2129;
  margin-bottom: 2px;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.history-item-time {
  font-size: 12px;
  color: #86909c;
}

.history-item-actions {
  opacity: 0;
  transition: opacity 0.2s;
  flex-shrink: 0;
}

.chat-history-item:hover .history-item-actions {
  opacity: 1;
}
</style>
