import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

const mocks = vi.hoisted(() => ({ post: vi.fn() }))

vi.mock('./api', () => ({ default: { post: mocks.post } }))
vi.mock('./authToken', () => ({
  broadcastAuthEvent: vi.fn(),
  clearSessionHint: vi.fn(),
}))
vi.mock('@/utils/authHandler', () => ({
  AuthHandler: { handleAuthFailure: vi.fn() },
}))

import { logService } from './logs'
import { FakeEventSource } from '@/test/FakeEventSource'

const flushConnect = async () => {
  await vi.advanceTimersByTimeAsync(0)
}

describe('logService connection lifecycle', () => {
  let ticketCounter: number

  beforeEach(() => {
    vi.useFakeTimers()
    FakeEventSource.reset()
    vi.stubGlobal('EventSource', FakeEventSource)
    ticketCounter = 0
    mocks.post.mockImplementation(async () => {
      ticketCounter += 1
      return { data: { data: { ticket: `ticket-${ticketCounter}` } } }
    })
  })

  afterEach(() => {
    vi.useRealTimers()
    vi.unstubAllGlobals()
    vi.clearAllMocks()
  })

  it('disconnect 发生在 ticket 请求完成前时不会创建孤儿 EventSource', async () => {
    let resolveTicket: ((value: unknown) => void) | undefined
    mocks.post.mockImplementationOnce(() => new Promise((resolve) => {
      resolveTicket = resolve
    }))
    const conn = logService.connectLogStream({ runId: 'run-1' })

    conn?.disconnect()
    resolveTicket?.({ data: { data: { ticket: 'late-ticket' } } })
    await flushConnect()

    expect(FakeEventSource.instances).toHaveLength(0)
  })

  it('overflow 关闭流后携带最后事件游标换新 ticket 重连', async () => {
    const states: string[] = []
    logService.connectLogStream({
      runId: 'run-1',
      onStateChange: (state) => states.push(state),
    })
    await flushConnect()
    const source = FakeEventSource.instances[0]

    source.emit('log_line', { data: { content: 'before overflow' } }, 'pg:2048')
    source.emit('stream_error', { code: 'overflow', message: '积压溢出' })
    source.onerror?.()
    await vi.advanceTimersByTimeAsync(1_100)

    expect(source.closed).toBe(true)
    expect(states).toContain('reconnecting')
    expect(FakeEventSource.instances).toHaveLength(2)
    expect(FakeEventSource.instances[1].url).toContain('ticket=ticket-2')
    expect(FakeEventSource.instances[1].url).toContain('cursor=pg%3A2048')
  })
})
