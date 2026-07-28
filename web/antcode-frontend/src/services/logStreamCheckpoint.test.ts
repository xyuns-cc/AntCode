import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

const mocks = vi.hoisted(() => ({ post: vi.fn() }))

vi.mock('./api', () => ({ default: { post: mocks.post } }))

import { logService } from './logs'
import { FakeEventSource } from '@/test/FakeEventSource'

const CHECKPOINTS = [
  ['history', 'pg:100'],
  ['recovery', 'pg:200'],
  ['gap', 'pg:300'],
] as const

const flushConnect = async (): Promise<void> => {
  await vi.advanceTimersByTimeAsync(0)
}

describe('logService SSE checkpoint', () => {
  beforeEach(() => {
    vi.useFakeTimers()
    FakeEventSource.reset()
    vi.stubGlobal('EventSource', FakeEventSource)
    mocks.post.mockResolvedValue({ data: { data: { ticket: 'ticket' } } })
  })

  afterEach(() => {
    vi.useRealTimers()
    vi.unstubAllGlobals()
    vi.clearAllMocks()
  })

  it.each(CHECKPOINTS)('%s checkpoint 更新重连游标且不产生 UI 副作用', async (sourceName, cursor) => {
    const onMessage = vi.fn()
    const onStatusUpdate = vi.fn()
    const onHistoricalLogsUpdate = vi.fn()
    const connection = logService.connectLogStream({
      runId: 'run-1',
      onMessage,
      onStatusUpdate,
      onHistoricalLogsUpdate,
    })
    await flushConnect()

    const source = FakeEventSource.instances[0]
    source.emit('stream_cursor', { type: 'stream_cursor', source: sourceName }, cursor)
    expect(onMessage).not.toHaveBeenCalled()
    expect(onStatusUpdate).not.toHaveBeenCalled()
    expect(onHistoricalLogsUpdate).not.toHaveBeenCalled()

    source.onerror?.()
    await vi.advanceTimersByTimeAsync(1_100)
    expect(FakeEventSource.instances[1].url).toContain(`cursor=${encodeURIComponent(cursor)}`)
    connection?.disconnect()
  })

  it('把服务端 ID 视为不透明值，按接收顺序保存而不解析或拒绝', async () => {
    const connection = logService.connectLogStream({ runId: 'run-1' })
    await flushConnect()

    const source = FakeEventSource.instances[0]
    source.emit('stream_cursor', { type: 'stream_cursor' }, 'future.cursor/v2~opaque')
    source.emit('stream_cursor', { type: 'stream_cursor' }, 'server-selected-checkpoint')
    source.onerror?.()
    await vi.advanceTimersByTimeAsync(1_100)

    expect(FakeEventSource.instances[1].url).toContain('cursor=server-selected-checkpoint')
    connection?.disconnect()
  })

  it('带游标恢复循环 recovery_unavailable 时累计计数并熔断，不再无限换票', async () => {
    const states: string[] = []
    logService.connectLogStream({ runId: 'run-1', onStateChange: (state) => states.push(state) })
    await flushConnect()

    const first = FakeEventSource.instances[0]
    first.emit('log_line', { type: 'log_line', data: { content: 'seed cursor' } }, 'pg:1')
    first.onerror?.()
    await vi.advanceTimersByTimeAsync(35_000)

    // 每轮：服务端固定先发 run_status 握手帧，再宣告恢复不可用并关流。
    for (let round = 0; round < 5; round++) {
      const es = FakeEventSource.instances[FakeEventSource.instances.length - 1]
      expect(es.url).toContain('cursor=pg%3A1')
      es.emit('run_status', { type: 'run_status', data: { status: 'running' } })
      es.emit('stream_error', {
        type: 'stream_error', code: 'recovery_unavailable', message: '断线日志暂时无法恢复，请稍后重试',
      })
      expect(es.closed).toBe(true)
      await vi.advanceTimersByTimeAsync(35_000)
    }

    expect(states).toContain('failed')
    expect(FakeEventSource.instances).toHaveLength(6)
  })
})
