export type LogStreamUrlOptions = Readonly<{
  apiBaseUrl: string
  runId: string
  ticket: string
  cursor?: string
}>

const normalizeBaseUrl = (baseUrl: string): string => baseUrl.replace(/\/$/, '')

export const buildLogStreamUrl = (options: LogStreamUrlOptions): string => {
  const query = new URLSearchParams({ ticket: options.ticket })
  if (options.cursor) query.set('cursor', options.cursor)
  const runId = encodeURIComponent(options.runId)
  const path = `/api/v1/logs/runs/${runId}/stream`
  return `${normalizeBaseUrl(options.apiBaseUrl)}${path}?${query.toString()}`
}
