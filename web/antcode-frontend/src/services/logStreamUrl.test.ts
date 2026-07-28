import { describe, expect, it } from 'vitest'
import { buildLogStreamUrl } from './logStreamUrl'

describe('buildLogStreamUrl', () => {
  it.each([
    ['', '/api/v1/logs/runs/run%2F1/stream?ticket=t%2B1'],
    ['/gateway/', '/gateway/api/v1/logs/runs/run%2F1/stream?ticket=t%2B1'],
    ['https://api.example.com', 'https://api.example.com/api/v1/logs/runs/run%2F1/stream?ticket=t%2B1'],
  ])('正确拼接 API base %s', (apiBaseUrl, expected) => {
    expect(buildLogStreamUrl({ apiBaseUrl, runId: 'run/1', ticket: 't+1' })).toBe(expected)
  })

  it('仅在已有游标时附加 cursor', () => {
    const url = buildLogStreamUrl({
      apiBaseUrl: '',
      runId: 'run-1',
      ticket: 'ticket-1',
      cursor: 'pg:2048',
    })
    expect(url).toBe(
      '/api/v1/logs/runs/run-1/stream?ticket=ticket-1&cursor=pg%3A2048',
    )
  })
})
