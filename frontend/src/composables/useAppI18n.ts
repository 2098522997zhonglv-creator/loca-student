import { computed } from 'vue';
import { useLocaleStore } from '@/store/localeStore';
import {
  getArcoLocale,
  toServerLanguage,
  translate,
  translateLegacyText,
  type MessageParams,
} from '@/i18n';

export const useAppI18n = () => {
  const localeStore = useLocaleStore();

  const locale = computed(() => localeStore.locale);
  const isEnglish = computed(() => localeStore.isEnglish);
  const arcoLocale = computed(() => getArcoLocale(locale.value));
  const serverLanguage = computed(() => toServerLanguage(locale.value));

  const t = (key: Parameters<typeof translate>[1], params?: MessageParams) => (
    translate(localeStore.locale, key, params)
  );

  const tl = (text: string) => translateLegacyText(text, localeStore.locale);

  return {
    locale,
    isEnglish,
    arcoLocale,
    serverLanguage,
    setLocale: localeStore.setLocale,
    toggleLocale: localeStore.toggleLocale,
    t,
    tl,
  };
};
