export type HealthResponse = {
  status: 'ok'
  service: string
  version: string
}

export type ErrorResponse = {
  error: {
    code: string
    message: string
    details: Record<string, unknown>
  }
}
