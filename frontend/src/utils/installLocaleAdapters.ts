import axios, { type AxiosRequestHeaders, type InternalAxiosRequestConfig } from 'axios';
import { Message, Modal } from '@arco-design/web-vue';
import { getApiBaseUrl } from '@/config/api';
import { DEFAULT_APP_LOCALE, getSavedLocale, toServerLanguage, translateLegacyText, type AppLocale } from '@/i18n';
import type { Pinia } from 'pinia';
import { useLocaleStore } from '@/store/localeStore';

declare global {
  interface Window {
    __wharttestFetchPatched__?: boolean;
    __wharttestAxiosPatched__?: boolean;
    __wharttestMessagePatched__?: boolean;
  }
}

type MessageMethod = 'success' | 'error' | 'info' | 'warning' | 'normal';
type ModalMethod = 'confirm' | 'info' | 'success' | 'warning' | 'error';
type LocalizablePayload = string | Record<string, unknown>;

const resolveLocale = (pinia?: Pinia): AppLocale => {
  try {
    return pinia ? useLocaleStore(pinia).locale : getSavedLocale();
  } catch {
    return getSavedLocale();
  }
};

const getServerLanguage = (pinia?: Pinia) => toServerLanguage(resolveLocale(pinia));

const shouldAttachLocale = (url: string) => {
  const apiBaseUrl = getApiBaseUrl();

  if (url.startsWith('/api')) {
    return true;
  }

  if (apiBaseUrl.startsWith('http://') || apiBaseUrl.startsWith('https://')) {
    return url.startsWith(apiBaseUrl);
  }

  try {
    const parsedUrl = new URL(url, window.location.origin);
    return parsedUrl.origin === window.location.origin && parsedUrl.pathname.startsWith('/api');
  } catch {
    return false;
  }
};

const mergeHeaders = (
  existingHeaders: HeadersInit | undefined,
  pinia?: Pinia,
) => {
  const headers = new Headers(existingHeaders ?? {});
  headers.set('Accept-Language', getServerLanguage(pinia));
  return headers;
};

const localizeText = (text: string, pinia?: Pinia) => (
  translateLegacyText(text, resolveLocale(pinia))
);

const localizeMessageInput = <T extends LocalizablePayload | undefined>(input: T, pinia?: Pinia): T => {
  if (typeof input === 'string') {
    return localizeText(input, pinia) as T;
  }

  if (input && typeof input === 'object') {
    const payload = { ...(input as Record<string, unknown>) };

    if (typeof payload.content === 'string') {
      payload.content = localizeText(payload.content, pinia);
    }

    if (typeof payload.title === 'string') {
      payload.title = localizeText(payload.title, pinia);
    }

    if (typeof payload.okText === 'string') {
      payload.okText = localizeText(payload.okText, pinia);
    }

    if (typeof payload.cancelText === 'string') {
      payload.cancelText = localizeText(payload.cancelText, pinia);
    }

    return payload as T;
  }

  return input;
};

const patchMessage = (pinia?: Pinia) => {
  if (typeof window !== 'undefined' && window.__wharttestMessagePatched__) {
    return;
  }

  (['success', 'error', 'info', 'warning', 'normal'] as MessageMethod[]).forEach((methodName) => {
    const originalMethod = Message[methodName] as (...args: any[]) => any;
    if (typeof originalMethod !== 'function') {
      return;
    }

    Message[methodName] = ((...args: any[]) => {
      const [firstArg, ...restArgs] = args;
      return originalMethod(localizeMessageInput(firstArg, pinia), ...restArgs);
    }) as typeof originalMethod;
  });

  (['confirm', 'info', 'success', 'warning', 'error'] as ModalMethod[]).forEach((methodName) => {
    const originalMethod = Modal[methodName] as (...args: any[]) => any;
    if (typeof originalMethod !== 'function') {
      return;
    }

    Modal[methodName] = ((...args: any[]) => {
      const [firstArg, ...restArgs] = args;
      return originalMethod(localizeMessageInput(firstArg, pinia), ...restArgs);
    }) as typeof originalMethod;
  });

  if (typeof window !== 'undefined') {
    window.__wharttestMessagePatched__ = true;
  }
};

const patchAxios = (pinia?: Pinia) => {
  if (typeof window !== 'undefined' && window.__wharttestAxiosPatched__) {
    return;
  }

  axios.interceptors.request.use((config: InternalAxiosRequestConfig) => {
    if (!config.url) {
      return config;
    }

    const baseUrl = config.baseURL ?? '';
    const targetUrl = baseUrl ? `${baseUrl}${config.url}` : config.url;
    if (!shouldAttachLocale(targetUrl)) {
      return config;
    }

    const headers = new Headers(config.headers as HeadersInit | undefined);
    headers.set('Accept-Language', getServerLanguage(pinia));
    config.headers = Object.fromEntries(headers.entries()) as AxiosRequestHeaders;
    return config;
  });

  if (typeof window !== 'undefined') {
    window.__wharttestAxiosPatched__ = true;
  }
};

const patchFetch = (pinia?: Pinia) => {
  if (typeof window === 'undefined' || window.__wharttestFetchPatched__) {
    return;
  }

  const originalFetch = window.fetch.bind(window);

  window.fetch = async (input: RequestInfo | URL, init?: RequestInit) => {
    const requestUrl = typeof input === 'string'
      ? input
      : input instanceof URL
        ? input.toString()
        : input.url;

    if (!shouldAttachLocale(requestUrl)) {
      return originalFetch(input, init);
    }

    if (input instanceof Request) {
      const request = new Request(input, {
        ...init,
        headers: mergeHeaders(init?.headers ?? input.headers, pinia),
      });
      return originalFetch(request);
    }

    return originalFetch(input, {
      ...init,
      headers: mergeHeaders(init?.headers, pinia),
    });
  };

  window.__wharttestFetchPatched__ = true;
};

export const installLocaleAdapters = (pinia?: Pinia) => {
  patchMessage(pinia);
  patchAxios(pinia);
  patchFetch(pinia);
};

export const getCurrentServerLanguage = (pinia?: Pinia) => getServerLanguage(pinia);

export const localizeRuntimeText = (text: string, pinia?: Pinia) => (
  localizeText(text, pinia)
);
