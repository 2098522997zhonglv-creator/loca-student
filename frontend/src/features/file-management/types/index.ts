export interface FileAsset {
  id: number;
  file_id: number;
  name: string;
  original_name: string;
  extension: string;
  mime_type: string;
  size: number;
  sha256: string;
  status: string;
  url: string;
  download_url: string;
  reference_count?: number;
  created_at: string;
  updated_at: string;
}


export interface FileManagementSetting {
  auto_delete_on_unbind: boolean;
  auto_delete_zero_refs: boolean;
  updated_at?: string;
}


export interface FileReferenceDetail {
  id: number;
  file_id: number;
  project: number;
  ref_type: string;
  ref_type_label: string;
  ref_id: string;
  object_id: number | string;
  object_name: string;
  description?: string;
  module_name?: string;
  page_name?: string;
  parent_id?: number | string;
  parent_name?: string;
  created_by?: string;
  created_at: string;
}
