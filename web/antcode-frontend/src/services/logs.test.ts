import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

const mocks = vi.hoisted(() => ({
  post: vi.fn(),
}))

vi.mock('./api', () => ({
  default: {
    post: mocks.post,
  },
}))

import { logService, type LogEntry } from './logs'

class FakeEventSource {
  static instances: FakeEventSource[] = []

  url: string
  closed = false
  onopen: (() => void) | null = null
  onerror: (() => void) | null = null
  private listeners = new Map<string, (event: MessageEvent) => void>()

  constructor(url: string) {
    this.url = url
    FakeEventSource.instances.push(this)
  }

  addEventListener(type: string, listener: (event: MessageEvent) => void) {
    this.listeners.set(type, listener)
  }

  emit(type: string, data: unknown) {
    this.listeners.get(type)?.({ data: JSON.stringify(data) } as MessageEvent)
  }

  close() {
    this.closed = true
  }
}

const flushConnect = async () => {
  // connectLogStream 内部先 await ticket 再 new EventSource，flush 微任务队列
  await vi.advanceTimersByTimeAsync(0)
}

describe('logService.connectLogStream (SSE)', () => {
  let ticketCounter: number

  beforeEach(() => {
    vi.useFakeTimers()
    FakeEventSource.instances = []
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

  it('每次连接都通过 stream-ticket 端点换取一次性票据并拼入 URL', async () => {
    const conn = logService.connectLogStream('run-1')
    await flushConnect()

    expect(mocks.post).toHaveBeenCalledWith('/api/v1/logs/stream-ticket')
    expect(FakeEventSource.instances).toHaveLength(1)
    expect(FakeEventSource.instances[0].url).toBe('/api/v1/logs/runs/run-1/stream?ticket=ticket-1')
    // JWT 永不进 URL
    expect(FakeEventSource.instances[0].url).not.toContain('token=')

    conn?.disconnect()
  })

  it('onerror 时无条件 close（防止 EventSource 用已消费的 ticket 自动重连），并换新 ticket 退避重连', async () => {
    const states: string[] = []
    const conn = logService.connectLogStream('run-1', undefined, undefined, (state) => states.push(state))
    await flushConnect()

    const first = FakeEventSource.instances[0]
    first.onerror?.()

    expect(first.closed).toBe(true)
    expect(states).toContain('reconnecting')

    // 第一次退避 1000ms 后用新 ticket 重连
    await vi.advanceTimersByTimeAsync(1100)
    expect(FakeEventSource.instances).toHaveLength(2)
    expect(FakeEventSource.instances[1].url).toBe('/api/v1/logs/runs/run-1/stream?ticket=ticket-2')

    conn?.disconnect()
  })

  it('disconnect 后不再重连', async () => {
    const conn = logService.connectLogStream('run-1')
    await flushConnect()

    conn?.disconnect()
    FakeEventSource.instances[0].onerror?.()
    await vi.advanceTimersByTimeAsync(60_000)

    expect(FakeEventSource.instances).toHaveLength(1)
    expect(FakeEventSource.instances[0].closed).toBe(true)
  })

  it('分发 log_line / run_status / 历史阶段事件到对应回调', async () => {
    const logs: LogEntry[] = []
    const statuses: Array<{ status: string }> = []
    const phases: string[] = []
    const conn = logService.connectLogStream(
      'run-1',
      (log) => logs.push(log),
      undefined,
      undefined,
      (status) => statuses.push(status),
      (update) => phases.push(update.phase)
    )
    await flushConnect()

    const es = FakeEventSource.instances[0]
    es.emit('historical_logs_start', { type: 'historical_logs_start' })
    es.emit('log_line', {
      type: 'log_line',
      data: { log_type: 'stdout', content: 'hello', timestamp: '2026-07-15T00:00:00Z', level: 'INFO', sequence: 1 },
    })
    es.emit('historical_logs_end', { type: 'historical_logs_end', sent_lines: 1 })
    es.emit('run_status', { type: 'run_status', data: { status: 'running', message: '执行中' } })
    es.emit('no_historical_logs', { type: 'no_historical_logs' })

    expect(phases).toEqual(['loading', 'loaded', 'empty'])
    expect(logs).toHaveLength(1)
    expect(logs[0].message).toBe('hello')
    expect(logs[0].log_type).toBe('stdout')
    expect(statuses).toEqual([{ status: 'running', message: '执行中', progress: undefined }])

    conn?.disconnect()
  })

  it('stream_error 事件走 onError 回调（与网络层 onerror 区分）', async () => {
    const errors: unknown[] = []
    const conn = logService.connectLogStream('run-1', undefined, (error) => errors.push(error))
    await flushConnect()

    FakeEventSource.instances[0].emit('stream_error', { type: 'stream_error', message: '会话已失效，连接终止' })

    expect(errors).toEqual(['会话已失效，连接终止'])

    conn?.disconnect()
  })

  it('超过 45s 无任何事件时 watchdog 判死重连', async () => {
    const conn = logService.connectLogStream('run-1')
    await flushConnect()

    const first = FakeEventSource.instances[0]
    // watchdog 每 15s 检查一次，60s 打点时 elapsed > 45s 成立 → close + 退避重连
    await vi.advanceTimersByTimeAsync(62_000)

    expect(first.closed).toBe(true)
    expect(FakeEventSource.instances.length).toBeGreaterThan(1)

    conn?.disconnect()
  })
})
