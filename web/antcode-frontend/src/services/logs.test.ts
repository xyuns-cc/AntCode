import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

const mocks = vi.hoisted(() => ({
  authFailure: vi.fn(),
  broadcastAuthEvent: vi.fn(),
  clearSessionHint: vi.fn(),
  post: vi.fn(),
}))

vi.mock('./api', () => ({
  default: {
    post: mocks.post,
  },
}))
vi.mock('./authToken', () => ({
  broadcastAuthEvent: mocks.broadcastAuthEvent,
  clearSessionHint: mocks.clearSessionHint,
}))
vi.mock('@/utils/authHandler', () => ({
  AuthHandler: { handleAuthFailure: mocks.authFailure },
}))

import { logService, type LogEntry } from './logs'
import { API_BASE_URL } from '@/utils/constants'
import { FakeEventSource } from '@/test/FakeEventSource'

const flushConnect = async () => {
  // connectLogStream 内部先 await ticket 再 new EventSource，flush 微任务队列
  await vi.advanceTimersByTimeAsync(0)
}

describe('logService.connectLogStream (SSE)', () => {
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

  it('每次连接都通过 stream-ticket 端点换取一次性票据并拼入 URL', async () => {
    const conn = logService.connectLogStream({ runId: 'run-1' })
    await flushConnect()

    expect(mocks.post).toHaveBeenCalledWith(
      '/api/v1/logs/stream-ticket',
      undefined,
      { params: { run_id: 'run-1' } },
    )
    expect(FakeEventSource.instances).toHaveLength(1)
    expect(FakeEventSource.instances[0].url).toBe(
      `${API_BASE_URL}/api/v1/logs/runs/run-1/stream?ticket=ticket-1`,
    )
    // JWT 永不进 URL
    expect(FakeEventSource.instances[0].url).not.toContain('token=')

    conn?.disconnect()
  })

  it('onerror 时无条件 close（防止 EventSource 用已消费的 ticket 自动重连），并换新 ticket 退避重连', async () => {
    const states: string[] = []
    const conn = logService.connectLogStream({
      runId: 'run-1',
      onStateChange: (state) => states.push(state),
    })
    await flushConnect()

    const first = FakeEventSource.instances[0]
    first.onerror?.()

    expect(first.closed).toBe(true)
    expect(states).toContain('reconnecting')

    // 第一次退避 1000ms 后用新 ticket 重连
    await vi.advanceTimersByTimeAsync(1100)
    expect(FakeEventSource.instances).toHaveLength(2)
    expect(FakeEventSource.instances[1].url).toBe(
      `${API_BASE_URL}/api/v1/logs/runs/run-1/stream?ticket=ticket-2`,
    )

    conn?.disconnect()
  })

  it('disconnect 后不再重连', async () => {
    const conn = logService.connectLogStream({ runId: 'run-1' })
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
    const historyUpdates: Array<{ phase: string; truncated?: boolean }> = []
    const conn = logService.connectLogStream({
      runId: 'run-1',
      onMessage: (log) => logs.push(log),
      onStatusUpdate: (status) => statuses.push(status),
      onHistoricalLogsUpdate: (update) => historyUpdates.push(update),
    })
    await flushConnect()

    const es = FakeEventSource.instances[0]
    es.emit('historical_logs_start', { type: 'historical_logs_start' })
    es.emit('log_line', {
      type: 'log_line',
      data: { log_type: 'stdout', content: 'hello', timestamp: '2026-07-15T00:00:00Z', level: 'INFO', sequence: 1 },
    })
    es.emit('historical_logs_end', { type: 'historical_logs_end', sent_lines: 1, truncated: true })
    es.emit('run_status', { type: 'run_status', data: { status: 'running', message: '执行中' } })
    es.emit('no_historical_logs', { type: 'no_historical_logs', truncated: true })

    expect(historyUpdates.map((update) => update.phase)).toEqual(['loading', 'loaded', 'empty'])
    expect(historyUpdates[1].truncated).toBe(true)
    expect(historyUpdates[2].truncated).toBe(true)
    expect(logs).toHaveLength(1)
    expect(logs[0].message).toBe('hello')
    expect(logs[0].log_type).toBe('stdout')
    expect(logs[0].sequence).toBe(1)
    expect(logs[0].id).toBe('run-1:stdout:1')
    expect(statuses).toEqual([{ status: 'running', message: '执行中', progress: undefined }])
    conn?.disconnect()
  })

  it('session_check_failed 错误可见，并在服务端关流后恢复连接', async () => {
    const errors: unknown[] = []
    const conn = logService.connectLogStream({
      runId: 'run-1',
      onError: (error) => errors.push(error),
    })
    await flushConnect()

    FakeEventSource.instances[0].emit('stream_error', {
      type: 'stream_error',
      code: 'session_check_failed',
      message: '会话校验服务不可用，连接终止',
    })

    const source = FakeEventSource.instances[0]
    expect(errors).toEqual(['会话校验服务不可用，连接终止'])
    expect(source.closed).toBe(false)
    source.onerror?.()
    await vi.advanceTimersByTimeAsync(1_100)
    expect(FakeEventSource.instances).toHaveLength(2)

    conn?.disconnect()
  })

  it('session_revoked 时换新票重连，由票据请求区分正常轮换和真实撤销', async () => {
    const states: string[] = []
    logService.connectLogStream({
      runId: 'run-1',
      onStateChange: (state) => states.push(state),
    })
    await flushConnect()

    const es = FakeEventSource.instances[0]
    es.emit('stream_error', { type: 'stream_error', code: 'session_revoked', message: '会话已失效，连接终止' })

    expect(es.closed).toBe(true)
    expect(states).toContain('reconnecting')
    expect(mocks.clearSessionHint).not.toHaveBeenCalled()
    expect(mocks.authFailure).not.toHaveBeenCalled()
    expect(mocks.broadcastAuthEvent).not.toHaveBeenCalled()

    // 服务端关流触发的 onerror 不得重复计时，退避后只创建一条新连接。
    es.onerror?.()
    await vi.advanceTimersByTimeAsync(1_100)
    expect(FakeEventSource.instances).toHaveLength(2)
    expect(FakeEventSource.instances[1].url).toContain('ticket=ticket-2')
  })

  it('access_revoked 只终止当前 run 日志流，不注销全局会话', async () => {
    const states: string[] = []
    logService.connectLogStream({
      runId: 'run-1',
      onStateChange: (state) => states.push(state),
    })
    await flushConnect()

    const source = FakeEventSource.instances[0]
    source.emit('stream_error', { type: 'stream_error', code: 'access_revoked', message: '无权访问该运行' })

    expect(source.closed).toBe(true)
    expect(states).toContain('failed')
    expect(mocks.clearSessionHint).not.toHaveBeenCalled()
    expect(mocks.authFailure).not.toHaveBeenCalled()
    expect(mocks.broadcastAuthEvent).not.toHaveBeenCalled()
  })
  it('瞬时 follower_unavailable 在服务端关流后换新 ticket 重连', async () => {
    const states: string[] = []
    const connection = logService.connectLogStream({ runId: 'run-1', onStateChange: (state) => states.push(state) })
    await flushConnect()
    const first = FakeEventSource.instances[0]
    first.emit('stream_error', { code: 'follower_unavailable', message: '实时日志服务不可用，请稍后重试' })
    first.onerror?.()
    expect(first.closed).toBe(true)
    expect(states).toContain('reconnecting')
    await vi.advanceTimersByTimeAsync(1_100)
    expect(FakeEventSource.instances).toHaveLength(2)
    expect(FakeEventSource.instances[1].url).toContain('ticket=ticket-2')
    connection?.disconnect()
  })
  it('历史刚完成就反复关流时熔断可达', async () => {
    const states: string[] = []
    logService.connectLogStream({
      runId: 'run-1',
      onStateChange: (state) => states.push(state),
    })
    await flushConnect()

    // run_status 是握手帧、历史完成也不代表实时链路健康，均不能清零重连计数，
    // 否则 recovery_unavailable 之类的换票循环永远达不到熔断。
    for (let round = 0; round < 6; round++) {
      const es = FakeEventSource.instances[FakeEventSource.instances.length - 1]
      es.onopen?.()
      es.emit('run_status', { type: 'run_status', data: { status: 'running' } })
      es.emit('historical_logs_end', { type: 'historical_logs_end', sent_lines: 0 })
      es.onerror?.()
      await vi.advanceTimersByTimeAsync(35_000) // 覆盖最大退避 30s + 抖动
    }

    expect(states).toContain('failed')
    expect(FakeEventSource.instances).toHaveLength(6)
  })

  it('历史完成后收到 ping 才重置熔断计数', async () => {
    logService.connectLogStream({ runId: 'run-1' })
    await flushConnect()

    for (let round = 0; round < 8; round++) {
      const es = FakeEventSource.instances[FakeEventSource.instances.length - 1]
      es.onopen?.()
      es.emit('historical_logs_end', { type: 'historical_logs_end', sent_lines: 0 })
      es.emit('ping', { type: 'ping' })
      es.onerror?.()
      await vi.advanceTimersByTimeAsync(2_000) // 计数已清零，退避恒为第一档 1s
    }

    expect(FakeEventSource.instances).toHaveLength(9)
    FakeEventSource.instances[8].onerror?.()
  })

  it('超过 45s 无任何事件时 watchdog 判死重连', async () => {
    const conn = logService.connectLogStream({ runId: 'run-1' })
    await flushConnect()

    const first = FakeEventSource.instances[0]
    // watchdog 每 15s 检查一次，60s 打点时 elapsed > 45s 成立 → close + 退避重连
    await vi.advanceTimersByTimeAsync(62_000)

    expect(first.closed).toBe(true)
    expect(FakeEventSource.instances.length).toBeGreaterThan(1)

    conn?.disconnect()
  })

  it('重复和延迟 onerror 不会创建额外连接或关闭新连接', async () => {
    const conn = logService.connectLogStream({ runId: 'run-1' })
    await flushConnect()
    const first = FakeEventSource.instances[0]

    first.onerror?.()
    first.onerror?.()
    await vi.advanceTimersByTimeAsync(1_100)

    expect(FakeEventSource.instances).toHaveLength(2)
    const second = FakeEventSource.instances[1]
    first.onerror?.()
    expect(second.closed).toBe(false)
    await vi.advanceTimersByTimeAsync(35_000)
    expect(FakeEventSource.instances).toHaveLength(2)
    conn?.disconnect()
  })

})
