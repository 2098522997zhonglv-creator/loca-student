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

export const normalizeListPayload = toArray
