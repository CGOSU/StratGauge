import { createContext, useContext, useMemo } from 'react'
import type { ReactNode } from 'react'
import { apiBaseUrl, createApiClient } from './client'

type ApiClient = ReturnType<typeof createApiClient>

const ApiClientContext = createContext<ApiClient | null>(null)

export function ApiClientProvider({ children }: { children: ReactNode }) {
  const client = useMemo(() => createApiClient(apiBaseUrl), [])
  return <ApiClientContext.Provider value={client}>{children}</ApiClientContext.Provider>
}

export function useApiClient(): ApiClient {
  const client = useContext(ApiClientContext)
  if (!client) {
    throw new Error('useApiClient must be used inside ApiClientProvider')
  }
  return client
}
