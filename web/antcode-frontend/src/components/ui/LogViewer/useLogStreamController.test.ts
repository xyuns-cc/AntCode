import { act, renderHook } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import type { LogStreamOptions } from '@/services/logs'
import { useLogStreamController } from './useLogStreamController'

const mocks = vi.hoisted(() => ({ connectLogStream: vi.fn() }))

vi.mock('@/services/logs', async (importOriginal) => {
  const original = await importOriginal<typeof import('@/services/logs')>()
  return { ...original, logService: { connectLogStream: mocks.connectLogStream } }
})

const disconnect = vi.fn()
const buffer = {
  addNotice: vi.fn(),
  addStreamMessage: vi.fn(),
  beginHistoryReplay: vi.fn(() => true),
  consumePendingResync: vi.fn(() => true),
  discardPendingResync: vi.fn(),
}
const options = {
  autoConnect: false,
  buffer,
  pendingOverflowCount: 0,
  runId: 'run-1',
  showStderr: true,
  showStdout: true,
}

const callbacks = (): LogStreamOptions => mocks.connectLogStream.mock.calls[0][0]

describe('useLogStreamController', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    buffer.beginHistoryReplay.mockReturnValue(true)
    buffer.consumePendingResync.mockReturnValue(true)
    mocks.connectLogStream.mockReturnValue({ disconnect })
  })

  it('终止型 failed 状态后恢复暂停不会绕过终止语义重连', () => {
    const { result } = renderHook(() => useLogStreamController(options))
    act(() => result.current.connect())
    act(() => callbacks().onStateChange?.('failed'))
    act(() => result.current.resumeAfterPause())

    expect(mocks.connectLogStream).toHaveBeenCalledTimes(1)
    expect(result.current.connectionStatus).toBe('error')
  })

  it('手动断开会清除暂停缺口且恢复暂停不会重连', () => {
    const { result } = renderHook(() => useLogStreamController(options))
    act(() => result.current.connect())
    act(() => result.current.disconnect())
    act(() => result.current.resumeAfterPause())

    expect(disconnect).toHaveBeenCalledOnce()
    expect(buffer.discardPendingResync).toHaveBeenCalledOnce()
    expect(mocks.connectLogStream).toHaveBeenCalledTimes(1)
    expect(result.current.connectionStatus).toBe('disconnected')
  })

  it('历史重放开始时委托缓冲区原子清空并更新加载状态', () => {
    const { result } = renderHook(() => useLogStreamController(options))
    act(() => result.current.connect())
    act(() => callbacks().onHistoricalLogsUpdate?.({ phase: 'loading' }))

    expect(buffer.beginHistoryReplay).toHaveBeenCalledOnce()
    expect(result.current.history).toEqual({ sentLines: 0, status: 'loading', truncated: false })
  })

  it('零行历史因字节容量截断时保留显式截断状态', () => {
    const { result } = renderHook(() => useLogStreamController(options))
    act(() => result.current.connect())
    act(() => callbacks().onHistoricalLogsUpdate?.({ phase: 'empty', sentLines: 0, truncated: true }))

    expect(result.current.history).toEqual({ sentLines: 0, status: 'empty', truncated: true })
    expect(buffer.addNotice).toHaveBeenCalledWith(expect.objectContaining({ type: 'warning' }))
  })
})
