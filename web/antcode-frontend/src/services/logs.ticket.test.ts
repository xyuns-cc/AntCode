import { afterEach, describe, expect, it, vi } from 'vitest'

const mocks = vi.hoisted(() => ({ post: vi.fn() }))

vi.mock('./api', () => ({
  default: { post: mocks.post },
}))

import { logService } from './logs'

const httpError = (status: number): object => ({ response: { status } })

describe('logService stream ticket', () => {
  afterEach(() => {
    vi.useRealTimers()
    vi.clearAllMocks()
  })

  it('签票 POST query 携带 run_id', async () => {
    mocks.post.mockResolvedValueOnce({ data: { data: { ticket: 'ticket-1' } } })

    await expect(logService.getStreamTicket('run-1')).resolves.toBe('ticket-1')
    expect(mocks.post).toHaveBeenCalledWith(
      '/api/v1/logs/stream-ticket',
      undefined,
      { params: { run_id: 'run-1' } },
    )
  })

  it('403 签票错误进入永久失败且不再重连', async () => {
    vi.useFakeTimers()
    const ticketError = httpError(403)
    mocks.post.mockRejectedValue(ticketError)
    const errors: unknown[] = []
    const states: string[] = []

    const connection = logService.connectLogStream({
      runId: 'run-1',
      onError: (error) => errors.push(error),
      onStateChange: (state) => states.push(state),
    })
    await vi.advanceTimersByTimeAsync(0)

    expect(errors).toEqual([ticketError])
    expect(states).toEqual(['error', 'failed'])
    await vi.advanceTimersByTimeAsync(60_000)
    expect(mocks.post).toHaveBeenCalledTimes(1)
    connection?.disconnect()
  })

  it('429 签票错误保持退避重连', async () => {
    vi.useFakeTimers()
    const ticketError = httpError(429)
    mocks.post.mockRejectedValue(ticketError)
    const states: string[] = []

    const connection = logService.connectLogStream({
      runId: 'run-1',
      onStateChange: (state) => states.push(state),
    })
    await vi.advanceTimersByTimeAsync(1_100)

    expect(mocks.post).toHaveBeenCalledTimes(2)
    expect(states).toContain('reconnecting')
    connection?.disconnect()
  })
})
