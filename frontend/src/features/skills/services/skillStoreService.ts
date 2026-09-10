import type { SkillStoreSource, SkillStoreManifest, ManifestSkill } from '../types'
import { request } from '@/utils/request'

const CUSTOM_SOURCES_KEY = 'wht_skill_store_custom_sources'
const ACTIVE_SOURCE_KEY = 'wht_skill_store_active_source_id'
const DEFAULT_SOURCE_ID = '__default__'

function normalizeBaseUrl(url: string): string {
  const trimmed = (url || '').trim()
  if (!trimmed) return ''
  return trimmed.endsWith('/') ? trimmed : trimmed + '/'
}

function readJSON<T>(key: string, fallback: T): T {
  try {
    const raw = localStorage.getItem(key)
    if (!raw) return fallback
    return JSON.parse(raw) as T
  } catch {
    return fallback
  }
}

function writeJSON(key: string, value: unknown): void {
  try {
    localStorage.setItem(key, JSON.stringify(value))
  } catch {
    /* ignore quota errors */
  }
}

export class SkillStoreService {
  /**
   * 获取自定义源列表（仅前端 localStorage，不包含默认源）
   */
  static getCustomSources(): SkillStoreSource[] {
    return readJSON<SkillStoreSource[]>(CUSTOM_SOURCES_KEY, [])
  }

  static setCustomSources(sources: SkillStoreSource[]): void {
    writeJSON(CUSTOM_SOURCES_KEY, sources)
  }

  static addCustomSource(name: string, baseUrl: string): SkillStoreSource {
    const list = SkillStoreService.getCustomSources()
    const source: SkillStoreSource = {
      id: `custom_${Date.now()}_${Math.random().toString(36).slice(2, 8)}`,
      name: name.trim() || baseUrl,
      baseUrl: normalizeBaseUrl(baseUrl),
    }
    list.push(source)
    SkillStoreService.setCustomSources(list)
    return source
  }

  static updateCustomSource(id: string, patch: Partial<Pick<SkillStoreSource, 'name' | 'baseUrl'>>): void {
    const list = SkillStoreService.getCustomSources().map(s => {
      if (s.id !== id) return s
      return {
        ...s,
        name: patch.name?.trim() || s.name,
        baseUrl: patch.baseUrl ? normalizeBaseUrl(patch.baseUrl) : s.baseUrl,
      }
    })
    SkillStoreService.setCustomSources(list)
  }

  static removeCustomSource(id: string): void {
    const list = SkillStoreService.getCustomSources().filter(s => s.id !== id)
    SkillStoreService.setCustomSources(list)
    if (SkillStoreService.getActiveSourceId() === id) {
      SkillStoreService.setActiveSourceId(DEFAULT_SOURCE_ID)
    }
  }

  static getActiveSourceId(): string {
    return localStorage.getItem(ACTIVE_SOURCE_KEY) || DEFAULT_SOURCE_ID
  }

  static setActiveSourceId(id: string): void {
    localStorage.setItem(ACTIVE_SOURCE_KEY, id)
  }

  /**
   * 拉取 manifest.json（通过后端代理，绕过浏览器 CORS）
   */
  static async fetchManifest(projectId: number, baseUrl: string): Promise<SkillStoreManifest> {
    const url = normalizeBaseUrl(baseUrl) + 'manifest.json'
    const response = await request<{ code: number; message: string; data: SkillStoreManifest }>({
      url: `/projects/${projectId}/skills/store-manifest/`,
      method: 'GET',
      params: { url },
    })
    const api = response.data as any
    if (response.success && api?.data) {
      const data = api.data
      if (!data || !Array.isArray(data.skills)) {
        throw new Error('manifest.json 缺少 skills 字段')
      }
      return data as SkillStoreManifest
    }
    throw new Error(api?.message || response.error || '加载 manifest 失败')
  }

  /**
   * 拉取单个 skill 的 README（通过后端代理）
   */
  static async fetchReadme(projectId: number, baseUrl: string, readmePath: string): Promise<string> {
    const url = normalizeBaseUrl(baseUrl) + readmePath.replace(/^\/+/, '')
    const response = await request<{ code: number; message: string; data: { content: string } }>({
      url: `/projects/${projectId}/skills/store-readme/`,
      method: 'GET',
      params: { url },
    })
    const api = response.data as any
    if (response.success && api?.data) {
      return api.data.content || ''
    }
    throw new Error(api?.message || response.error || '加载 README 失败')
  }

  /**
   * 把 manifest 中的相对 zip_path 拼成完整 URL
   */
  static resolveZipUrl(baseUrl: string, zipPath: string): string {
    return normalizeBaseUrl(baseUrl) + zipPath.replace(/^\/+/, '')
  }

  static normalizeBaseUrl(url: string): string {
    return normalizeBaseUrl(url)
  }

  /**
   * 标记 manifest 中已安装的 skill（按 name 匹配）
   */
  static markInstalled(
    manifestSkills: ManifestSkill[],
    installed: Array<{ id: number; name: string }>
  ): Array<{ item: ManifestSkill; installed: boolean; installedId: number | null }> {
    const map = new Map(installed.map(s => [s.name, s.id]))
    return manifestSkills.map(item => ({
      item,
      installed: map.has(item.name),
      installedId: map.get(item.name) ?? null,
    }))
  }
}

export const SKILL_STORE_DEFAULT_SOURCE_ID = DEFAULT_SOURCE_ID
