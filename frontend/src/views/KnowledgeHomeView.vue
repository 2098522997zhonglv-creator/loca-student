<template>
  <div>
    <h1>本地知识中心</h1>
    <p class="lead">从 WHartTest 提取的项目级知识库、LLM 配置与需求评审系统。</p>
    <a-row :gutter="16">
      <a-col v-for="card in cards" :key="card.title" :span="6">
        <a-card hoverable @click="$router.push(card.path)">
          <a-statistic :title="card.title" :value="card.value" />
          <p>{{ card.description }}</p>
        </a-card>
      </a-col>
    </a-row>
  </div>
</template>

<script setup lang="ts">
const cards = [
  { title: '项目空间', value: 'Project', description: '项目成员、角色与数据隔离', path: '/projects' },
  { title: '知识库', value: 'RAG', description: '文档分块、向量化和混合检索', path: '/knowledge-management' },
  { title: '需求评审', value: 'Review', description: '需求拆分、专项分析与问题报告', path: '/requirements' },
  { title: '智能问答', value: 'Agent', description: '结合知识库和项目上下文对话', path: '/langgraph-chat' },
]
</script>

<style scoped>
h1 { margin-top: 0; } .lead { color: #4e5969; margin-bottom: 24px; } .arco-card { cursor: pointer; min-height: 160px; }
</style>
