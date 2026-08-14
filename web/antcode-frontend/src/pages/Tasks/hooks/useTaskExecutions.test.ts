import { act, renderHook, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import { taskService } from '@/services/tasks'
import { useTaskExecutions } from './useTaskExecutions'

vi.mock('@/services/tasks', () => ({
  taskService: { getTaskRuns: vi.fn() },
}))

describe('useTaskExecutions', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    vi.mocked(taskService.getTaskRuns).mockResolvedValue({
      items: [],
      total: 45,
      page: 1,
      size: 20,
    })
  })

  it('requests the selected backend page', async () => {
    const { result } = renderHook(() => useTaskExecutions('task-1'))
    await waitFor(() =>
      expect(taskService.getTaskRuns).toHaveBeenCalledWith('task-1', { page: 1, size: 20 })
    )

    act(() => result.current.changePage(2, 20))

    await waitFor(() =>
      expect(taskService.getTaskRuns).toHaveBeenLastCalledWith('task-1', { page: 2, size: 20 })
    )
    expect(result.current.pagination.total).toBe(45)
  })
})
