<template>
  <div class="locale-toggle" :aria-label="t('locale.switcherLabel')">
    <button
      type="button"
      class="locale-toggle-button"
      :class="{ active: locale === 'zh-CN' }"
      :title="t('locale.switchToChinese')"
      :aria-pressed="locale === 'zh-CN'"
      @click="setLocale('zh-CN')"
    >
      中
    </button>
    <button
      type="button"
      class="locale-toggle-button"
      :class="{ active: locale === 'en-US' }"
      :title="t('locale.switchToEnglish')"
      :aria-pressed="locale === 'en-US'"
      @click="setLocale('en-US')"
    >
      EN
    </button>
  </div>
</template>

<script setup lang="ts">
import { useAppI18n } from '@/composables/useAppI18n';

const { locale, setLocale, t } = useAppI18n();
</script>

<style scoped>
.locale-toggle {
  display: inline-flex;
  align-items: center;
  padding: 2px;
  gap: 2px;
  border-radius: 999px;
  background: rgba(15, 23, 42, 0.06);
  border: 1px solid rgba(15, 23, 42, 0.08);
}

.locale-toggle-button {
  min-width: 34px;
  height: 28px;
  border: none;
  border-radius: 999px;
  background: transparent;
  color: var(--theme-text-secondary, #4e5969);
  font-size: 12px;
  font-weight: 700;
  letter-spacing: 0.04em;
  cursor: pointer;
  transition: background-color 0.2s ease, color 0.2s ease, transform 0.2s ease;
}

.locale-toggle-button:hover {
  color: var(--theme-text, #1d2129);
}

.locale-toggle-button.active {
  background: rgba(var(--theme-accent-rgb, 22, 93, 255), 0.14);
  color: var(--theme-accent, #165dff);
}
</style>
