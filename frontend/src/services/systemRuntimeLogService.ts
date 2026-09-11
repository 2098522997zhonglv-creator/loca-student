import { request } from '@/utils/request';
import { useAuthStore } from '@/store/authStore';
import { getSmartApiBaseUrl } from '@/config/api';

export interface RuntimeLogFileInfo {
  name: string;
  size: number;
  modified_at: number;
}

export interface RuntimeLogTailResponse {
  file: string;
  size: number;
  modified_at: number;
  lines: string[];
  line_count: number;
  level?: string | null;
}

export async function fetchRuntimeLogTail(params: {
  file?: string;
  lines?: number;
  level?: string;
} = {}): Promise<{ success: boolean; data?: RuntimeLogTailResponse; error?: string }> {
  return request({
    url: '/operation-logs/runtime/',
    method: 'GET',
    params: {
      file: params.file || 'app.log',
      lines: params.lines ?? 200,
      level: params.level || undefined,
    },
  });
}

export async function fetchRuntimeLogFiles(): Promise<{
  success: boolean;
  data?: { results: RuntimeLogFileInfo[] } | RuntimeLogFileInfo[];
  error?: string;
}> {
  return request({
    url: '/operation-logs/runtime/files/',
    method: 'GET',
  });
}

export async function downloadRuntimeLogFile(file = 'app.log'): Promise<void> {
  const authStore = useAuthStore();
  const token = authStore.getAccessToken;
  const url = `${getSmartApiBaseUrl()}/operation-logs/runtime/download/?file=${encodeURIComponent(file)}`;
  const response = await fetch(url, {
    headers: token ? { Authorization: `Bearer ${token}` } : {},
  });
  if (!response.ok) {
    throw new Error(`下载失败: ${response.status}`);
  }
  const blob = await response.blob();
  const objectUrl = URL.createObjectURL(blob);
  const link = document.createElement('a');
  link.href = objectUrl;
  link.download = file;
  document.body.appendChild(link);
  link.click();
  link.remove();
  URL.revokeObjectURL(objectUrl);
}
