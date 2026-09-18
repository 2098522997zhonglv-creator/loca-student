<template>
  <a-modal
    v-model:visible="modalVisible"
    :title="modalText.title"
    :width="600"
    :mask-closable="false"
    :confirm-loading="loading"
    @before-ok="handleConfirm"
    @cancel="handleCancel"
  >
    <div class="execution-confirm-content">
      <a-alert type="info" style="margin-bottom: 16px;">
        <template #icon>
          <icon-info-circle />
        </template>
        {{ modalText.infoMessage }}
      </a-alert>

      <a-descriptions :column="1" bordered>
        <a-descriptions-item :label="modalText.testSuite">
          <strong>{{ suite?.name }}</strong>
        </a-descriptions-item>
        <a-descriptions-item :label="modalText.suiteDescription">
          {{ suite?.description || '-' }}
        </a-descriptions-item>
        <a-descriptions-item :label="modalText.caseCountLabel">
          <a-tag color="blue">{{ modalText.caseCount(suite?.testcase_count || 0) }}</a-tag>
        </a-descriptions-item>
        <a-descriptions-item :label="modalText.estimatedTimeLabel">
          <a-tag color="orange">{{ modalText.estimatedTime(estimatedTime) }}</a-tag>
        </a-descriptions-item>
      </a-descriptions>

      <a-alert type="warning" style="margin-top: 16px;">
        <template #icon>
          <icon-exclamation-circle />
        </template>
        <div>
          <p style="margin-bottom: 8px;">{{ modalText.notesTitle }}</p>
          <ul style="margin: 0; padding-left: 20px;">
            <li>{{ modalText.noteResource }}</li>
            <li>{{ modalText.noteCancelable }}</li>
            <li>{{ modalText.noteRecorded }}</li>
          </ul>
        </div>
      </a-alert>
    </div>
  </a-modal>
</template>

<script setup lang="ts">
import { ref, computed } from 'vue';
import { Message } from '@arco-design/web-vue';
import { IconInfoCircle, IconExclamationCircle } from '@arco-design/web-vue/es/icon';
import { useAppI18n } from '@/composables/useAppI18n';
import { createTestExecution } from '@/services/testExecutionService';
import type { TestSuite } from '@/services/testSuiteService';

interface Props {
  visible: boolean;
  currentProjectId: number | null;
  suite: TestSuite | null;
}

const props = defineProps<Props>();
const { isEnglish, tl } = useAppI18n();

const emit = defineEmits<{
  (e: 'update:visible', value: boolean): void;
  (e: 'success', executionId: number): void;
}>();

const modalText = computed(() => (
  isEnglish.value
    ? {
        title: 'Confirm test suite execution',
        infoMessage: 'The test will run asynchronously in the background. You can track its progress in the execution history.',
        testSuite: 'Test Suites',
        suiteDescription: 'Suite description',
        caseCountLabel: 'Case count',
        caseCount: (count: number) => `${count} ${count === 1 ? 'case' : 'cases'}`,
        estimatedTimeLabel: 'Estimated time',
        estimatedTime: (minutes: number) => `~ ${minutes} min`,
        notesTitle: 'Execution notes:',
        noteResource: 'Test execution will consume system resources.',
        noteCancelable: 'You can cancel the execution at any time.',
        noteRecorded: 'Each test case result and screenshot will be recorded.',
        missingRequiredInfo: 'Missing required information',
        executionStarted: 'Test execution has started',
        startExecutionFailed: 'Failed to start execution',
        startExecutionError: 'An error occurred while starting the execution',
      }
    : {
        title: '确认执行测试套件',
        infoMessage: '测试将在后台异步执行,您可以在执行历史中查看进度',
        testSuite: '测试套件',
        suiteDescription: '套件描述',
        caseCountLabel: '用例数量',
        caseCount: (count: number) => `${count} 个用例`,
        estimatedTimeLabel: '预计耗时',
        estimatedTime: (minutes: number) => `约 ${minutes} 分钟`,
        notesTitle: '执行注意事项:',
        noteResource: '测试执行期间会占用系统资源',
        noteCancelable: '执行过程中可以随时取消',
        noteRecorded: '每个用例的执行结果和截图将被记录',
        missingRequiredInfo: '缺少必要信息',
        executionStarted: '测试执行已启动',
        startExecutionFailed: '启动执行失败',
        startExecutionError: '启动执行时发生错误',
      }
));

const modalVisible = computed({
  get: () => props.visible,
  set: (value) => emit('update:visible', value),
});

const loading = ref(false);

// 预估执行时间 (每个用例约30秒)
const estimatedTime = computed(() => {
  if (!props.suite) return 0;
  const totalCount = props.suite.testcase_count || 0;
  const minutes = Math.ceil((totalCount * 30) / 60);
  return minutes;
});

// 确认执行
const handleConfirm = async () => {
  if (!props.currentProjectId || !props.suite) {
    Message.error(modalText.value.missingRequiredInfo);
    return false;
  }

  loading.value = true;
  try {
    const response = await createTestExecution(props.currentProjectId, {
      suite_id: props.suite.id,
    });

    if (response.success && response.data) {
      Message.success(tl(response.message || modalText.value.executionStarted));
      emit('success', response.data.id);
      handleCancel();
      return true;
    } else {
      Message.error(tl(response.error || modalText.value.startExecutionFailed));
      return false;
    }
  } catch (error) {
    console.error('启动执行失败:', error);
    Message.error(modalText.value.startExecutionError);
    return false;
  } finally {
    loading.value = false;
  }
};

// 取消
const handleCancel = () => {
  emit('update:visible', false);
};
</script>

<style scoped>
.execution-confirm-content {
  padding: 8px 0;
}
</style>