import request from '@/utils/request';
import type { FileAsset } from '../types';

const base = (projectId: number | string) => `/projects/${projectId}/files`;

export const fileService = {
  list(projectId: number | string, params?: Record<string, any>) {
    return request.get<FileAsset[]>(`${base(projectId)}/`, { params });
  },

  upload(projectId: number | string, files: File[]) {
    const formData = new FormData();
    files.forEach((file) => formData.append('files', file));
    return request.post<FileAsset | FileAsset[]>(`${base(projectId)}/`, formData);
  },

  validate(projectId: number | string, fileIds: number[]) {
    return request.post<{ valid: boolean; files: FileAsset[] }>(`${base(projectId)}/validate/`, { file_ids: fileIds });
  },

  references(projectId: number | string, fileId: number) {
    return request.get(`${base(projectId)}/${fileId}/references/`);
  },

  delete(projectId: number | string, fileId: number) {
    return request.delete(`${base(projectId)}/${fileId}/`);
  },

  getSettings(projectId: number | string) {
    return request.get(`${base(projectId)}/settings/`);
  },

  updateSettings(projectId: number | string, data: { auto_delete_on_unbind?: boolean; auto_delete_zero_refs?: boolean }) {
    return request.post(`${base(projectId)}/settings/`, data);
  },

  cleanupUnreferenced(projectId: number | string) {
    return request.post(`${base(projectId)}/cleanup-unreferenced/`);
  },
};
