import { act, renderHook } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import type { LogMessage } from './enhancedLogViewerTypes'
import { truncateLogContent, useLogMessageBuffer } from './useLogMessageBuffer'

const entry = (sequence: number, content = `line-${sequence}`): LogMessage => ({
  id: `log-${sequence}-${content}`,
  type: 'stdout',
  content,
  timestamp: '2026-07-16T00:00:00.000Z',
  sequence,
})

describe('useLogMessageBuffer', () => {
  beforeEach(() => {
    vi.useFakeTimers()
    vi.stubGlobal('requestAnimationFrame', (callback: FrameRequestCallback) => (
      setTimeout(() => callback(performance.now()), 16)
    ))
    vi.stubGlobal('cancelAnimationFrame', (handle: number) => clearTimeout(handle))
  })

  afterEach(() => {
    vi.useRealTimers()
    vi.unstubAllGlobals()
  })

  it('按动画帧批量提交并按 type/sequence 去重', () => {
    const { result } = renderHook(() => useLogMessageBuffer({ maxLines: 10 }))
    act(() => {
      result.current.api.addStreamMessage(entry(1))
      result.current.api.addStreamMessage(entry(1, 'duplicate'))
      result.current.api.addStreamMessage(entry(2))
    })
    expect(result.current.messages).toEqual([])

    act(() => vi.advanceTimersByTime(16))
    expect(result.current.messages.map((item) => item.sequence)).toEqual([1, 2])
  })

  it('sequence 0 参与去重（后端计数从 0 起），仅无 sequence 的消息跳过去重', () => {
    const noSequence = (content: string): LogMessage => ({
      id: `no-seq-${content}`,
      type: 'stdout',
      content,
      timestamp: '2026-07-16T00:00:00.000Z',
    })
    const { result } = renderHook(() => useLogMessageBuffer({ maxLines: 10 }))
    act(() => {
      result.current.api.addStreamMessage(entry(0, 'zero-a'))
      result.current.api.addStreamMessage(entry(0, 'zero-b'))
      result.current.api.addStreamMessage(noSequence('no-seq-a'))
      result.current.api.addStreamMessage(noSequence('no-seq-b'))
      vi.advanceTimersByTime(16)
    })
    expect(result.current.messages.map((item) => item.content)).toEqual([
      'zero-a', 'no-seq-a', 'no-seq-b',
    ])
  })

  it('暂停时只为真实流数据标记重同步，界面通知不触发重连', () => {
    const { result } = renderHook(() => useLogMessageBuffer({ maxLines: 10 }))
    act(() => {
      result.current.setPaused(true)
      result.current.addNotice(entry(1))
    })
    expect(result.current.api.consumePendingResync()).toBe(false)

    act(() => result.current.api.addStreamMessage(entry(2)))
    expect(result.current.api.consumePendingResync()).toBe(true)
    expect(result.current.api.consumePendingResync()).toBe(false)
  })

  it('每帧提交后严格保留 maxLines 条最新日志', () => {
    const { result } = renderHook(() => useLogMessageBuffer({ maxLines: 2 }))
    act(() => {
      result.current.api.addStreamMessage(entry(1))
      result.current.api.addStreamMessage(entry(2))
      result.current.api.addStreamMessage(entry(3))
      vi.advanceTimersByTime(16)
    })
    expect(result.current.messages.map((item) => item.sequence)).toEqual([2, 3])
  })

  it('按 UTF-8 字节安全截断且不保留超限原文', () => {
    const bounded = truncateLogContent('你'.repeat(10000))
    expect(new TextEncoder().encode(bounded).byteLength).toBeLessThanOrEqual(16 * 1024)
    expect(bounded).toContain('前端截断')
    expect(bounded).not.toContain('\ufffd')
  })

  it('使用原始流 identity 区分降级后的未知日志类型', () => {
    const { result } = renderHook(() => useLogMessageBuffer({ maxLines: 10 }))
    act(() => {
      result.current.api.addStreamMessage({
        ...entry(7, 'custom-a'), type: 'info', sequenceIdentity: 'run-1:custom-a:7',
      })
      result.current.api.addStreamMessage({
        ...entry(7, 'custom-b'), type: 'info', sequenceIdentity: 'run-1:custom-b:7',
      })
      vi.advanceTimersByTime(16)
    })
    expect(result.current.messages.map((item) => item.content)).toEqual(['custom-a', 'custom-b'])
  })
})
