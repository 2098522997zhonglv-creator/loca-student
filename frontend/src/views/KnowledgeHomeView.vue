<template>
  <div class="home">
    <section class="hero">
      <div class="hero-copy">
        <p class="eyebrow">Local Knowledge Studio</p>
        <h1>本地知识中心</h1>
        <p class="lead">
          项目知识、智能问答与需求评审，汇于同一工作台。
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
        <div class="visual-glow" />
        <div class="visual-card">
          <div class="visual-row" />
          <div class="visual-row short" />
          <div class="visual-meta">
            <span>知识检索</span>
            <span>需求评审</span>
            <span>Agent</span>
          </div>
        </div>
      </div>
    </section>

    <section class="entries" aria-label="快捷入口">
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
        <span class="entry-go" aria-hidden="true">→</span>
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
  gap: 22px;
  padding: 20px;
  box-sizing: border-box;
}

.hero {
  position: relative;
  overflow: hidden;
  display: grid;
  grid-template-columns: 1.15fr 0.85fr;
  gap: 20px;
  min-height: min(44vh, 380px);
  padding: 36px 40px;
  border-radius: 22px;
  background:
    linear-gradient(135deg, #0b1f1a 0%, #0f3d38 52%, #0f766e 100%);
  color: #eefaf6;
}

.hero-copy {
  position: relative;
  z-index: 1;
  display: flex;
  flex-direction: column;
  justify-content: center;
  max-width: 520px;
}

.eyebrow {
  margin: 0 0 12px;
  font-size: 11px;
  letter-spacing: 0.16em;
  text-transform: uppercase;
  color: rgba(126, 224, 200, 0.88);
}

h1 {
  margin: 0;
  font-family: var(--kc-display);
  font-size: clamp(32px, 4.4vw, 48px);
  font-weight: 700;
  line-height: 1.1;
  letter-spacing: -0.02em;
}

.lead {
  margin: 14px 0 0;
  font-size: 15px;
  line-height: 1.7;
  color: rgba(232, 247, 242, 0.78);
  max-width: 36em;
}

.cta-row {
  display: flex;
  flex-wrap: wrap;
  gap: 10px;
  margin-top: 24px;
}

.cta-primary {
  background: #7ee0c8 !important;
  border-color: #7ee0c8 !important;
  color: #0b1f1a !important;
  font-weight: 600;
  border-radius: 10px !important;
  height: 36px !important;
  transition: transform 0.16s ease, box-shadow 0.16s ease !important;
}

.cta-primary:hover {
  transform: translateY(-1px);
  box-shadow: 0 8px 18px rgba(126, 224, 200, 0.28) !important;
}

.cta-secondary {
  background: transparent !important;
  border-color: rgba(232, 247, 242, 0.28) !important;
  color: #e8f7f2 !important;
  border-radius: 10px !important;
  height: 36px !important;
}

.cta-secondary:hover {
  background: rgba(255, 255, 255, 0.08) !important;
  border-color: rgba(232, 247, 242, 0.45) !important;
}

.hero-visual {
  position: relative;
  min-height: 200px;
}

.visual-glow {
  position: absolute;
  width: 220px;
  height: 220px;
  right: 8%;
  top: 10%;
  border-radius: 50%;
  background: rgba(126, 224, 200, 0.22);
  filter: blur(8px);
  animation: glow-pulse 6s ease-in-out infinite;
}

.visual-card {
  position: absolute;
  inset: 16% 6% 14% 18%;
  border-radius: 18px;
  padding: 24px;
  background: rgba(255, 255, 255, 0.08);
  border: 1px solid rgba(255, 255, 255, 0.14);
  backdrop-filter: blur(10px);
  display: flex;
  flex-direction: column;
  justify-content: flex-end;
  gap: 12px;
  animation: card-in 0.55s ease both;
}

.visual-row {
  height: 8px;
  border-radius: 999px;
  background: rgba(232, 247, 242, 0.22);
}

.visual-row.short {
  width: 54%;
}

.visual-meta {
  display: flex;
  gap: 8px;
  flex-wrap: wrap;
  margin-top: 4px;
}

.visual-meta span {
  padding: 5px 10px;
  border-radius: 8px;
  font-size: 12px;
  background: rgba(126, 224, 200, 0.14);
  color: #d9fff3;
}

.entries {
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  gap: 12px;
}

.entry {
  appearance: none;
  position: relative;
  border: 1px solid rgba(15, 61, 56, 0.08);
  background: #fff;
  border-radius: 16px;
  padding: 20px 18px 18px;
  text-align: left;
  cursor: pointer;
  display: flex;
  flex-direction: column;
  gap: 6px;
  min-height: 148px;
  transition: transform 0.18s ease, box-shadow 0.18s ease, border-color 0.18s ease;
  font-family: inherit;
}

.entry:hover,
.entry:focus-visible {
  transform: translateY(-2px);
  border-color: rgba(15, 118, 110, 0.28);
  box-shadow: 0 12px 28px rgba(15, 61, 56, 0.08);
  outline: none;
}

.entry:hover .entry-go,
.entry:focus-visible .entry-go {
  opacity: 1;
  transform: translateX(0);
}

.entry-kicker {
  font-size: 11px;
  letter-spacing: 0.12em;
  text-transform: uppercase;
  color: #0f766e;
}

.entry-title {
  font-family: var(--kc-display);
  font-size: 22px;
  color: var(--kc-ink);
}

.entry-desc {
  font-size: 13px;
  line-height: 1.55;
  color: var(--theme-text-secondary);
}

.entry-go {
  position: absolute;
  right: 16px;
  bottom: 14px;
  font-size: 16px;
  color: #0f766e;
  opacity: 0;
  transform: translateX(-4px);
  transition: opacity 0.18s ease, transform 0.18s ease;
}

@keyframes glow-pulse {
  0%,
  100% {
    opacity: 0.7;
    transform: scale(1);
  }
  50% {
    opacity: 1;
    transform: scale(1.05);
  }
}

@keyframes card-in {
  from {
    opacity: 0;
    transform: translateY(10px);
  }
  to {
    opacity: 1;
    transform: translateY(0);
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
    padding: 26px 20px;
    min-height: auto;
  }
  .hero-visual {
    min-height: 150px;
  }
  .entries {
    grid-template-columns: 1fr;
  }
}
</style>
