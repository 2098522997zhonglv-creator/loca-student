/** Compatibility shim for restored testcase UI imports. */
export { toArray, normalizeListPayload } from '@/utils/responseHelpers'
import { normalizeListPayload } from '@/utils/responseHelpers'

export function throwIfFailed(res: any): void {
  if (res && res.success === false) {
    const err: any = new Error(res.error || res.message || '操作失败')
    err.errors = res.errors
    throw err
  }
}

export function wrapListResponse<T = any>(res: any) {
  throwIfFailed(res)
  const { results, count } = normalizeListPayload<T>(res?.data ?? res)
  return { data: { results, count }, status: 'success' as const, message: '' }
}

export function wrapOneResponse<T = any>(res: any) {
  throwIfFailed(res)
  return { data: (res?.data ?? null) as T | null, status: 'success' as const, message: '' }
}
