export type ApiErrorPayload = {
  error?: {
    code?: string
    message?: string
    details?: Record<string, unknown>
  }
}

export class ApiClientError extends Error {
  readonly status: number
  readonly code: string | undefined
  readonly details: Record<string, unknown> | undefined

  constructor(status: number, payload: ApiErrorPayload | undefined) {
    super(payload?.error?.message ?? `请求失败：${status}`)
    this.name = 'ApiClientError'
    this.status = status
    this.code = payload?.error?.code
    this.details = payload?.error?.details
  }
}

export const apiBaseUrl = import.meta.env.VITE_API_BASE_URL
  ? import.meta.env.VITE_API_BASE_URL.replace(/\/$/, '')
  : '/api'

export const healthUrl = `${apiBaseUrl}/healthz`

export type FetchLike = (input: RequestInfo | URL, init?: RequestInit) => Promise<Response>

export async function requestJson<T>(
  path: string,
  init?: RequestInit,
  fetcher: FetchLike = fetch,
): Promise<T> {
  const response = await fetcher(resolveUrl(path), init)
  const payload = await readPayload(response)
  if (!response.ok) {
    throw new ApiClientError(response.status, payload as ApiErrorPayload | undefined)
  }
  return payload as T
}

export function createApiClient(
  baseUrl: string = apiBaseUrl,
  fetcher: FetchLike = fetch,
) {
  const normalizedBaseUrl = baseUrl.replace(/\/$/, '')
  return {
    baseUrl: normalizedBaseUrl,
    requestJson: <T>(path: string, init?: RequestInit) =>
      requestJson<T>(`${normalizedBaseUrl}${path.startsWith('/') ? path : `/${path}`}`, init, fetcher),
  }
}

function resolveUrl(path: string): string {
  if (/^https?:\/\//i.test(path)) {
    return path
  }
  return `${apiBaseUrl}${path.startsWith('/') ? path : `/${path}`}`
}

async function readPayload(response: Response): Promise<unknown> {
  if (response.status === 204) {
    return undefined
  }
  const contentType = response.headers.get('content-type') ?? ''
  if (!contentType.includes('json')) {
    return undefined
  }
  try {
    return await response.json()
  } catch {
    return undefined
  }
}
