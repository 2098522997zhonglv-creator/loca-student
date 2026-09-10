/** Normalize DRF list responses (plain arrays or paginated envelopes). */
export function toArray<T>(payload: unknown): T[] {
  if (Array.isArray(payload)) return payload as T[]
  if (payload && typeof payload === 'object') {
    const value = payload as Record<string, unknown>
    if (Array.isArray(value.results)) return value.results as T[]
    if (value.data && typeof value.data === 'object') return toArray<T>(value.data)
  }
  return []
}

export interface NormalizedListPayload<T> {
  results: T[]
  count: number
}

/** Return both list items and total count for paginated or plain-array payloads. */
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
    if (value.data !== undefined) {
      return normalizeListPayload<T>(value.data)
    }
  }
  return { results: [], count: 0 }
}
