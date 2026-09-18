<template>
  <div class="testcase-selector">
    <div class="selector-header">
      <a-input-search
        v-model="searchKeyword"
        :placeholder="tableText.searchPlaceholder"
        allow-clear
        style="width: 300px;"
        @search="handleSearch"
      />
      <a-select
        v-model="selectedModule"
        :placeholder="tableText.moduleFilter"
        allow-clear
        :loading="modulesLoading"
        style="width: 180px; margin-left: 12px;"
        @change="handleModuleChange"
      >
        <a-option
          v-for="module in flatModuleList"
          :key="module.id"
          :value="module.id"
        >
          {{ module.indentName }}
        </a-option>
      </a-select>
      <a-select
        v-model="selectedLevel"
        :placeholder="tableText.priorityFilter"
        allow-clear
        style="width: 150px; margin-left: 12px;"
        @change="handleLevelChange"
      >
        <a-option v-for="option in priorityOptions" :key="option.value" :value="option.value">
          {{ option.label }}
        </a-option>
      </a-select>
      <a-select
        v-model="selectedTestType"
        :placeholder="tableText.testTypeFilter"
        allow-clear
        style="width: 150px; margin-left: 12px;"
        @change="handleTestTypeChange"
      >
        <a-option v-for="option in localizedTestTypeOptions" :key="option.value" :value="option.value">
          {{ option.label }}
        </a-option>
      </a-select>
    </div>

    <a-table
      :columns="columns"
      :data="testCaseData"
      :pagination="paginationConfig"
      :loading="loading"
      :key="`suite-case-selector-${locale}`"
      :scroll="{ y: 400 }"
      :bordered="{ cell: true }"
      row-key="id"
      @page-change="onPageChange"
      @page-size-change="onPageSizeChange"
    >
      <template #selection="{ record }">
        <a-checkbox
          :model-value="localSelectedIds.includes(record.id)"
          @change="(checked: boolean) => handleCheckboxChange(record.id, checked)"
        />
      </template>
      <template #selectAll>
        <a-checkbox
          :model-value="isCurrentPageAllSelected"
          :indeterminate="isCurrentPageIndeterminate"
          @change="handleSelectCurrentPage"
        />
      </template>
      <template #level="{ record }">
        <a-tag :color="getLevelColor(record.level)">{{ record.level }}</a-tag>
      </template>
      <template #testType="{ record }">
        <a-tag>{{ getLocalizedTestTypeLabel(record.test_type) }}</a-tag>
      </template>
    </a-table>

    <div class="selector-footer">
      <div class="selected-info">
        {{ tableText.selectedPrefix }} <strong>{{ localSelectedIds.length }}</strong> {{ tableText.selectedSuffix }}
      </div>
      <a-space>
        <a-button @click="handleCancel">{{ tableText.cancel }}</a-button>
        <a-button
          type="primary"
          :disabled="localSelectedIds.length === 0"
          @click="handleConfirm"
        >
          {{ tableText.confirm }}
        </a-button>
      </a-space>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref, reactive, computed, onMounted, watch } from 'vue';
import { Message } from '@arco-design/web-vue';
import { getTestCaseList, type TestCase } from '@/services/testcaseService';
import { getTestCaseModules, type TestCaseModule } from '@/services/testcaseModuleService';
import { formatDate, getLevelColor, TEST_TYPE_OPTIONS } from '@/utils/formatters';
import { useAppI18n } from '@/composables/useAppI18n';

interface Props {
  currentProjectId: number | null;
  initialSelectedIds?: number[];
}

const props = withDefaults(defineProps<Props>(), {
  initialSelectedIds: () => [],
});

const emit = defineEmits<{
  (e: 'confirm', selectedIds: number[]): void;
  (e: 'cancel'): void;
}>();
const { locale, isEnglish } = useAppI18n();

const tableText = computed(() => (
  isEnglish.value
    ? {
        searchPlaceholder: 'Search case name',
        moduleFilter: 'Filter module',
        priorityFilter: 'Filter priority',
        testTypeFilter: 'Filter test type',
        selectedPrefix: 'Selected',
        selectedSuffix: 'test case(s)',
        cancel: 'Cancel',
        confirm: 'Confirm',
        select: 'Select',
        caseName: 'Case Name',
        precondition: 'Precondition',
        priority: 'Priority',
        testType: 'Test Type',
        creator: 'Created By',
        createdAt: 'Created At',
        fetchCasesFailed: 'Failed to fetch test case list',
        fetchCasesError: 'An error occurred while fetching test case list',
        priorityLabels: {
          P0: 'P0 - Highest',
          P1: 'P1 - High',
          P2: 'P2 - Medium',
          P3: 'P3 - Low',
        } as Record<string, string>,
        testTypeLabels: {
          smoke: 'Smoke Test',
          functional: 'Functional Test',
          boundary: 'Boundary Test',
          exception: 'Exception Test',
          permission: 'Permission Test',
          security: 'Security Test',
          compatibility: 'Compatibility Test',
        } as Record<string, string>,
      }
    : {
        searchPlaceholder: '搜索用例名称',
        moduleFilter: '筛选模块',
        priorityFilter: '筛选优先级',
        testTypeFilter: '筛选测试类型',
        selectedPrefix: '已选择',
        selectedSuffix: '个测试用例',
        cancel: '取消',
        confirm: '确认选择',
        select: '选择',
        caseName: '用例名称',
        precondition: '前置条件',
        priority: '优先级',
        testType: '测试类型',
        creator: '创建者',
        createdAt: '创建时间',
        fetchCasesFailed: '获取测试用例列表失败',
        fetchCasesError: '获取测试用例列表时发生错误',
        priorityLabels: {
          P0: 'P0 - 最高',
          P1: 'P1 - 高',
          P2: 'P2 - 中',
          P3: 'P3 - 低',
        } as Record<string, string>,
        testTypeLabels: {
          smoke: '冒烟测试',
          functional: '功能测试',
          boundary: '边界测试',
          exception: '异常测试',
          permission: '权限测试',
          security: '安全测试',
          compatibility: '兼容性测试',
        } as Record<string, string>,
      }
));

const loading = ref(false);
const modulesLoading = ref(false);
const searchKeyword = ref('');
const selectedLevel = ref<string>('');
const selectedTestType = ref<string>('');
const selectedModule = ref<number | undefined>(undefined);
const testCaseData = ref<TestCase[]>([]);
const localSelectedIds = ref<number[]>([...props.initialSelectedIds]);
const moduleList = ref<TestCaseModule[]>([]);

const paginationConfig = reactive({
  total: 0,
  current: 1,
  pageSize: 10,
  showTotal: true,
  showJumper: true,
  showPageSize: true,
  pageSizeOptions: [10, 20, 50],
});

const priorityOptions = computed(() => ([
  { value: 'P0', label: tableText.value.priorityLabels.P0 },
  { value: 'P1', label: tableText.value.priorityLabels.P1 },
  { value: 'P2', label: tableText.value.priorityLabels.P2 },
  { value: 'P3', label: tableText.value.priorityLabels.P3 },
]));

const localizedTestTypeOptions = computed(() => (
  TEST_TYPE_OPTIONS.map((option) => ({
    ...option,
    label: tableText.value.testTypeLabels[option.value] || option.label,
  }))
));

const getLocalizedTestTypeLabel = (testType?: string): string => {
  if (!testType) return tableText.value.testTypeLabels.functional;
  return tableText.value.testTypeLabels[testType] || testType;
};

const columns = computed(() => ([
  {
    title: tableText.value.select,
    slotName: 'selection',
    width: 50,
    titleSlotName: 'selectAll',
    align: 'center' as const,
  },
  { title: 'ID', dataIndex: 'id', width: 60 },
  { title: tableText.value.caseName, dataIndex: 'name', width: 200, ellipsis: true, tooltip: true },
  { title: tableText.value.precondition, dataIndex: 'precondition', width: 150, ellipsis: true, tooltip: true },
  { title: tableText.value.priority, dataIndex: 'level', slotName: 'level', width: 80 },
  { title: tableText.value.testType, dataIndex: 'test_type', slotName: 'testType', width: 90 },
  {
    title: tableText.value.creator,
    dataIndex: 'creator_detail',
    render: ({ record }: { record: TestCase }) => record.creator_detail?.username || '-',
    width: 100,
  },
  {
    title: tableText.value.createdAt,
    dataIndex: 'created_at',
    render: ({ record }: { record: TestCase }) => formatDate(record.created_at),
    width: 150,
  },
]));

// 当前页是否全选
const isCurrentPageAllSelected = computed(() => {
  if (testCaseData.value.length === 0) return false;
  return testCaseData.value.every((item) => localSelectedIds.value.includes(item.id));
});

// 当前页是否半选状态
const isCurrentPageIndeterminate = computed(() => {
  const currentPageSelectedCount = testCaseData.value.filter((item) =>
    localSelectedIds.value.includes(item.id)
  ).length;
  return currentPageSelectedCount > 0 && currentPageSelectedCount < testCaseData.value.length;
});

// 处理单个复选框变化
const handleCheckboxChange = (id: number, checked: boolean) => {
  if (checked) {
    if (!localSelectedIds.value.includes(id)) {
      localSelectedIds.value.push(id);
    }
  } else {
    const index = localSelectedIds.value.indexOf(id);
    if (index > -1) {
      localSelectedIds.value.splice(index, 1);
    }
  }
};

// 处理当前页全选/取消全选
const handleSelectCurrentPage = (checked: boolean) => {
  if (checked) {
    const currentPageIds = testCaseData.value.map((item) => item.id);
    currentPageIds.forEach((id) => {
      if (!localSelectedIds.value.includes(id)) {
        localSelectedIds.value.push(id);
      }
    });
  } else {
    const currentPageIds = testCaseData.value.map((item) => item.id);
    localSelectedIds.value = localSelectedIds.value.filter((id) => !currentPageIds.includes(id));
  }
};

// 获取测试用例列表
const fetchTestCases = async () => {
  if (!props.currentProjectId) {
    testCaseData.value = [];
    paginationConfig.total = 0;
    return;
  }

  loading.value = true;
  try {
    const response = await getTestCaseList(props.currentProjectId, {
      page: paginationConfig.current,
      pageSize: paginationConfig.pageSize,
      search: searchKeyword.value,
      level: selectedLevel.value || undefined,
      test_type: selectedTestType.value || undefined,
      module_id: selectedModule.value,
    });

    if (response.success && response.data) {
      testCaseData.value = response.data;
      paginationConfig.total = response.total || response.data.length;
    } else {
      Message.error(response.error || tableText.value.fetchCasesFailed);
      testCaseData.value = [];
      paginationConfig.total = 0;
    }
  } catch (error) {
    console.error('获取测试用例列表出错:', error);
    Message.error(tableText.value.fetchCasesError);
    testCaseData.value = [];
    paginationConfig.total = 0;
  } finally {
    loading.value = false;
  }
};

const handleSearch = () => {
  paginationConfig.current = 1;
  fetchTestCases();
};

const handleLevelChange = () => {
  paginationConfig.current = 1;
  fetchTestCases();
};

const handleTestTypeChange = () => {
  paginationConfig.current = 1;
  fetchTestCases();
};

const handleModuleChange = () => {
  paginationConfig.current = 1;
  fetchTestCases();
};

// 将树形模块列表扁平化为带缩进的列表
const flatModuleList = computed(() => {
  const flatList: Array<TestCaseModule & { indentName: string }> = [];
  
  const flatten = (modules: TestCaseModule[], level: number = 0) => {
    modules.forEach((module) => {
      const indent = '　'.repeat(level); // 使用全角空格缩进
      flatList.push({
        ...module,
        indentName: `${indent}${module.name}`
      });
      if (module.children && module.children.length > 0) {
        flatten(module.children, level + 1);
      }
    });
  };
  
  flatten(moduleList.value);
  return flatList;
});

// 加载模块列表
const fetchModules = async () => {
  if (!props.currentProjectId) {
    moduleList.value = [];
    return;
  }

  modulesLoading.value = true;
  try {
    const response = await getTestCaseModules(props.currentProjectId);
    if (response.success && response.data) {
      moduleList.value = response.data;
    } else {
      console.error('获取模块列表失败:', response.error);
      moduleList.value = [];
    }
  } catch (error) {
    console.error('获取模块列表出错:', error);
    moduleList.value = [];
  } finally {
    modulesLoading.value = false;
  }
};

const onPageChange = (page: number) => {
  paginationConfig.current = page;
  fetchTestCases();
};

const onPageSizeChange = (pageSize: number) => {
  paginationConfig.pageSize = pageSize;
  paginationConfig.current = 1;
  fetchTestCases();
};

const handleConfirm = () => {
  emit('confirm', [...localSelectedIds.value]);
};

const handleCancel = () => {
  emit('cancel');
};

onMounted(() => {
  fetchModules();
  fetchTestCases();
});

watch(
  () => props.currentProjectId,
  () => {
    paginationConfig.current = 1;
    searchKeyword.value = '';
    selectedLevel.value = '';
    selectedTestType.value = '';
    selectedModule.value = undefined;
    fetchModules();
    fetchTestCases();
  }
);
</script>

<style scoped>
.testcase-selector {
  display: flex;
  flex-direction: column;
  gap: 16px;
}

.selector-header {
  display: flex;
  align-items: center;
}

.selector-footer {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding-top: 16px;
  border-top: 1px solid var(--color-border);
}

.selected-info {
  font-size: 14px;
  color: var(--color-text-2);
}
</style>
