import { act, renderHook } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

const apiMocks = vi.hoisted(() => ({ list: vi.fn() }))
vi.mock('./api', () => ({ userManagementApi: apiMocks }))

import { useUserList } from './useUserList'

const emptyPage = {
  items: [],
  pagination: { page: 1, size: 20, total: 0, pages: 0 },
}

describe('useUserList', () => {
  beforeEach(() => {
    vi.useFakeTimers()
    apiMocks.list.mockResolvedValue(emptyPage)
  })

  afterEach(() => {
    vi.useRealTimers()
    vi.resetAllMocks()
  })

  it('debounces search and requests the filtered first page from the server', async () => {
    const { result } = renderHook(() => useUserList(true))
    await act(() => vi.advanceTimersByTimeAsync(300))

    act(() => result.current.dispatch({ type: 'search', value: 'page-two-user' }))
    await act(() => vi.advanceTimersByTimeAsync(299))
    expect(apiMocks.list).toHaveBeenCalledTimes(1)

    await act(() => vi.advanceTimersByTimeAsync(1))
    expect(apiMocks.list).toHaveBeenLastCalledWith({
      page: 1,
      size: 20,
      search: 'page-two-user',
      sortField: null,
      sortOrder: 'asc',
    })
  })
})
