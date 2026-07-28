import { renderHook, waitFor } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import type { LogMessage } from './enhancedLogViewerTypes'
import { useVirtualizedList } from './useVirtualizedList'

const messages: LogMessage[] = Array.from({ length: 10 }, (_, index) => ({
  id: String(index),
  type: 'stdout',
  content: `line-${index}`,
  timestamp: '2026-07-16T00:00:00.000Z',
}))

const useList = (items: LogMessage[]) => useVirtualizedList({
  items,
  height: 60,
  estimatedItemHeight: 20,
  searchText: '',
  displayOptions: { showTimestamp: true, showLevel: true, showSource: true },
})

describe('useVirtualizedList', () => {
  it('挂载后计算总高度和首屏缓冲范围', async () => {
    const { result } = renderHook(() => useList(messages))
    await waitFor(() => expect(result.current.state.totalHeight).toBe(200))
    expect(result.current.positions.visibleRange).toEqual({ start: 0, end: 5 })
  })

  it('列表清空时同步清空布局并返回空范围', async () => {
    const { result, rerender } = renderHook(
      ({ items }) => useList(items),
      { initialProps: { items: messages } },
    )
    await waitFor(() => expect(result.current.state.totalHeight).toBe(200))
    rerender({ items: [] })
    await waitFor(() => expect(result.current.state.totalHeight).toBe(0))
    expect(result.current.positions.visibleRange).toEqual({ start: 0, end: -1 })
  })
})
