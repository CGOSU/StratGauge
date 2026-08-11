import { useCallback, useEffect, useState } from 'react'
import { useApiClient } from '@/api/provider'

type ApiResourceState<T> = {
  data: T | null
  error: Error | null
  loading: boolean
}

export function useApiResource<T>(path: string): ApiResourceState<T> & { refresh: () => void } {
  const client = useApiClient()
  const [state, setState] = useState<ApiResourceState<T>>({
    data: null,
    error: null,
    loading: true,
  })
  const [refreshToken, setRefreshToken] = useState(0)
  const refresh = useCallback(() => setRefreshToken((value) => value + 1), [])

  useEffect(() => {
    const controller = new AbortController()
    setState((current) => ({ ...current, error: null, loading: true }))
    void client
      .requestJson<T>(path, { signal: controller.signal })
      .then((data) => setState({ data, error: null, loading: false }))
      .catch((reason: unknown) => {
        if (reason instanceof DOMException && reason.name === 'AbortError') {
          return
        }
        setState({
          data: null,
          error: reason instanceof Error ? reason : new Error('请求失败。'),
          loading: false,
        })
      })
    return () => controller.abort()
  }, [client, path, refreshToken])

  return { ...state, refresh }
}
