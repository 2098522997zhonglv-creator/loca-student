/** Normalize DRF list responses (plain arrays or paginated envelopes). */
export function toArray<T>(payload: unknown): T[] {
  return normalizeListPayload<T>(payload).results
}

export interface NormalizedListPayload<T> {
  results: T[]
  count: number
}

/**
 * Return both list items and total count for:
 * - plain arrays
 * - DRF `{ results, count }`
 * - unified API `{ status, data: [...] | { results, count } }`
 * - request() unwrap `{ success, data: ... }`
 */
export function normalizeListPayload<T>(payload: unknown): NormalizedListPayload<T> {
  if (Array.isArray(payload)) {
    return { results: payload as T[], count: payload.length }
  }
  if (payload && typeof payload === 'object') {
    const value = payload as Record<string, unknown>

    if (Array.isArray(value.results)) {
      const count =
        typeof value.count === 'number'
          ? value.count
          : typeof value.total === 'number'
            ? value.total
            : value.results.length
      return { results: value.results as T[], count }
    }

    // Unified envelope from backend renderer
    if ('status' in value && 'data' in value) {
      return normalizeListPayload<T>(value.data)
    }

    // request() interceptor unwrap
    if ('success' in value && 'data' in value) {
      const nested = normalizeListPayload<T>(value.data)
      if (nested.results.length || nested.count) {
        return nested
      }
      if (typeof value.total === 'number' && Array.isArray(value.data)) {
        return { results: value.data as T[], count: value.total }
      }
      return nested
    }

    if (value.data !== undefined) {
      return normalizeListPayload<T>(value.data)
    }
  }
  return { results: [], count: 0 }
}

/** True when payload looks like a successful unified/list response (not an error blob). */
export function isSuccessfulListPayload(payload: unknown): boolean {
  if (Array.isArray(payload)) return true
  if (!payload || typeof payload !== 'object') return false
  const value = payload as Record<string, unknown>
  if (value.status === 'error' || value.success === false) return false
  if (Array.isArray(value.results) || Array.isArray(value.data)) return true
  if (value.status === 'success') return true
  if (value.data && typeof value.data === 'object') {
    const data = value.data as Record<string, unknown>
    return Array.isArray(data.results) || Array.isArray(data)
  }
  return false
}

export type ParsedListResponse<T> =
  | { ok: true; results: T[]; count: number; code?: number }
  | { ok: false; error: string; code?: number }

/**
 * Parse axios `response.data` for list APIs.
 * Accepts unified `{ status, data }` and raw DRF `{ results, count }`.
 */
export function parseListAxiosData<T>(
  axiosData: unknown,
  fallbackError = '响应数据格式不正确'
): ParsedListResponse<T> {
  if (axiosData == null) {
    return { ok: false, error: fallbackError }
  }
  if (Array.isArray(axiosData)) {
    return { ok: true, results: axiosData as T[], count: axiosData.length }
  }
  if (typeof axiosData !== 'object') {
    return { ok: false, error: fallbackError }
  }

  const value = axiosData as Record<string, unknown>
  if (value.status === 'error') {
    return {
      ok: false,
      error: typeof value.message === 'string' ? value.message : fallbackError,
      code: typeof value.code === 'number' ? value.code : undefined,
    }
  }

  if (isSuccessfulListPayload(axiosData)) {
    const { results, count } = normalizeListPayload<T>(axiosData)
    return {
      ok: true,
      results,
      count,
      code: typeof value.code === 'number' ? value.code : undefined,
    }
  }

  return {
    ok: false,
    error: typeof value.message === 'string' ? value.message : fallbackError,
    code: typeof value.code === 'number' ? value.code : undefined,
  }
}
