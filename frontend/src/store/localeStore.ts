import { computed, ref } from 'vue';
import { defineStore } from 'pinia';
import {
  DEFAULT_APP_LOCALE,
  LOCALE_STORAGE_KEY,
  applyDocumentLanguage,
  getSavedLocale,
  type AppLocale,
} from '@/i18n';

const isBrowser = () => typeof window !== 'undefined';

export const useLocaleStore = defineStore('locale', () => {
  const locale = ref<AppLocale>(DEFAULT_APP_LOCALE);
  const isEnglish = computed(() => locale.value === 'en-US');

  const setLocale = (nextLocale: AppLocale) => {
    locale.value = nextLocale;
    applyDocumentLanguage(nextLocale);

    if (isBrowser()) {
      window.localStorage.setItem(LOCALE_STORAGE_KEY, nextLocale);
    }
  };

  const toggleLocale = () => {
    setLocale(locale.value === 'zh-CN' ? 'en-US' : 'zh-CN');
  };

  const initializeLocale = () => {
    setLocale(getSavedLocale());
  };

  return {
    locale,
    isEnglish,
    setLocale,
    toggleLocale,
    initializeLocale,
  };
});
