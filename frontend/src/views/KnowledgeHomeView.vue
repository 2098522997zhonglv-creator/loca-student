<template>
  <div class="home">
    <section class="hero">
      <div class="hero-copy">
        <p class="eyebrow">Local Knowledge Studio</p>
        <h1>本地知识中心</h1>
        <p class="lead">
          把项目知识、LLM 对话与需求评审收拢到同一工作台。选好项目，从知识库或问答开始。
        </p>
        <div class="cta-row">
          <a-button type="primary" class="cta-primary" @click="$router.push('/langgraph-chat')">
            开始知识问答
          </a-button>
          <a-button class="cta-secondary" @click="$router.push('/knowledge-management')">
            管理知识库
          </a-button>
        </div>
      </div>
      <div class="hero-visual" aria-hidden="true">
        <div class="orb orb-a" />
        <div class="orb orb-b" />
        <div class="panel">
          <div class="panel-line" />
          <div class="panel-line short" />
          <div class="panel-chips">
            <span>RAG</span>
            <span>Review</span>
            <span>Agent</span>
          </div>
        </div>
      </div>
    </section>

    <section class="entries">
      <button
        v-for="item in entries"
        :key="item.path"
        type="button"
        class="entry"
        @click="$router.push(item.path)"
      >
        <span class="entry-kicker">{{ item.kicker }}</span>
        <span class="entry-title">{{ item.title }}</span>
        <span class="entry-desc">{{ item.description }}</span>
      </button>
    </section>
  </div>
</template>

<script setup lang="ts">
const entries = [
  {
    kicker: 'Knowledge',
    title: '知识库',
    description: '上传文档、分块向量化，支持混合检索。',
    path: '/knowledge-management',
  },
  {
    kicker: 'Conversation',
    title: '知识问答',
    description: '结合项目上下文与知识库的 Agent 对话。',
    path: '/langgraph-chat',
  },
  {
    kicker: 'Requirements',
    title: '需求评审',
    description: '模块拆分与六维专项分析报告。',
    path: '/requirements',
  },
  {
    kicker: 'Workspace',
    title: '项目管理',
    description: '成员角色与项目级数据隔离。',
    path: '/projects',
  },
]
</script>

<style scoped>
.home {
  display: flex;
  flex-direction: column;
  gap: 28px;
}

.hero {
  position: relative;
  overflow: hidden;
  display: grid;
  grid-template-columns: 1.2fr 0.8fr;
  gap: 24px;
  min-height: min(52vh, 460px);
  padding: 42px 44px;
  border-radius: 28px;
  background:
    linear-gradient(135deg, rgba(11, 31, 26, 0.96), rgba(15, 61, 56, 0.92) 48%, rgba(15, 118, 110, 0.88)),
    radial-gradient(600px 280px at 80% 20%, rgba(126, 224, 200, 0.35), transparent 60%);
  color: #eefaf6;
  box-shadow: 0 24px 48px rgba(15, 61, 56, 0.22);
}

.hero-copy {
  position: relative;
  z-index: 1;
  display: flex;
  flex-direction: column;
  justify-content: center;
  max-width: 560px;
  text-align: left;
}

.eyebrow {
  margin: 0 0 14px;
  font-size: 12px;
  letter-spacing: 0.16em;
  text-transform: uppercase;
  color: rgba(126, 224, 200, 0.9);
}

h1 {
  margin: 0;
  font-family: var(--kc-display);
  font-size: clamp(36px, 5vw, 54px);
  font-weight: 700;
  line-height: 1.08;
  letter-spacing: -0.02em;
}

.lead {
  margin: 16px 0 0;
  font-size: 16px;
  line-height: 1.7;
  color: rgba(232, 247, 242, 0.82);
}

.cta-row {
  display: flex;
  flex-wrap: wrap;
  gap: 12px;
  margin-top: 28px;
}

.cta-primary {
  background: #7ee0c8 !important;
  border-color: #7ee0c8 !important;
  color: #0b1f1a !important;
  font-weight: 600;
}

.cta-secondary {
  background: transparent !important;
  border-color: rgba(232, 247, 242, 0.35) !important;
  color: #e8f7f2 !important;
}

.hero-visual {
  position: relative;
  min-height: 240px;
}

.orb {
  position: absolute;
  border-radius: 50%;
  filter: blur(2px);
  animation: float 7s ease-in-out infinite;
}

.orb-a {
  width: 180px;
  height: 180px;
  right: 18%;
  top: 12%;
  background: rgba(126, 224, 200, 0.28);
}

.orb-b {
  width: 110px;
  height: 110px;
  right: 8%;
  bottom: 18%;
  background: rgba(255, 255, 255, 0.12);
  animation-delay: -2.5s;
}

.panel {
  position: absolute;
  inset: 18% 8% 16% 22%;
  border-radius: 22px;
  padding: 28px;
  background: rgba(255, 255, 255, 0.08);
  border: 1px solid rgba(255, 255, 255, 0.16);
  backdrop-filter: blur(8px);
  display: flex;
  flex-direction: column;
  justify-content: flex-end;
  gap: 14px;
  animation: panel-in 0.7s ease both;
}

.panel-line {
  height: 10px;
  border-radius: 999px;
  background: rgba(232, 247, 242, 0.28);
}

.panel-line.short {
  width: 58%;
}

.panel-chips {
  display: flex;
  gap: 8px;
  flex-wrap: wrap;
}

.panel-chips span {
  padding: 6px 10px;
  border-radius: 999px;
  font-size: 12px;
  letter-spacing: 0.04em;
  background: rgba(126, 224, 200, 0.16);
  color: #d9fff3;
}

.entries {
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  gap: 14px;
}

.entry {
  appearance: none;
  border: 1px solid rgba(15, 61, 56, 0.08);
  background: rgba(255, 255, 255, 0.82);
  border-radius: 20px;
  padding: 22px 20px;
  text-align: left;
  cursor: pointer;
  display: flex;
  flex-direction: column;
  gap: 8px;
  min-height: 168px;
  transition: transform 0.2s ease, box-shadow 0.2s ease, border-color 0.2s ease;
  font-family: inherit;
}

.entry:hover {
  transform: translateY(-3px);
  border-color: rgba(15, 118, 110, 0.28);
  box-shadow: 0 16px 32px rgba(15, 61, 56, 0.1);
}

.entry-kicker {
  font-size: 11px;
  letter-spacing: 0.14em;
  text-transform: uppercase;
  color: #0f766e;
}

.entry-title {
  font-family: var(--kc-display);
  font-size: 24px;
  color: var(--kc-ink);
}

.entry-desc {
  font-size: 14px;
  line-height: 1.6;
  color: var(--theme-text-secondary);
}

@keyframes float {
  0%,
  100% {
    transform: translateY(0);
  }
  50% {
    transform: translateY(-12px);
  }
}

@keyframes panel-in {
  from {
    opacity: 0;
    transform: translateY(12px) scale(0.98);
  }
  to {
    opacity: 1;
    transform: translateY(0) scale(1);
  }
}

@media (max-width: 1100px) {
  .entries {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }
}

@media (max-width: 840px) {
  .hero {
    grid-template-columns: 1fr;
    padding: 28px 22px;
    min-height: auto;
  }
  .hero-visual {
    min-height: 180px;
  }
  .entries {
    grid-template-columns: 1fr;
  }
}
</style>
