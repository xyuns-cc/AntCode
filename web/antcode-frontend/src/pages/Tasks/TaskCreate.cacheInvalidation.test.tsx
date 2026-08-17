import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router'
import { describe, expect, it, vi } from 'vitest'

import TaskCreate from './TaskCreate'

const createTask = vi.fn().mockResolvedValue({ id: 'task-1' })

vi.mock('@/services/tasks', () => ({
  taskService: {
    createTask: (...args: unknown[]) => createTask(...args),
  },
}))

vi.mock('@/services/projects', () => ({
  projectService: {
    getAllProjects: vi.fn().mockResolvedValue([
      { id: 'project-1', name: 'demo-project', project_type: 'rule' },
    ]),
  },
}))

vi.mock('@/services/workers', () => ({
  workerService: {
    getMyAvailableWorkers: vi.fn().mockResolvedValue([]),
  },
}))

// 任务列表用的 queryKey，和 useTasksQuery 一致：['tasks', params]。
const TASK_LIST_KEY = ['tasks', { page: 1, size: 10 }]

describe('TaskCreate 创建后必须失效任务列表缓存', () => {
  it('提交成功后把 ["tasks"] 缓存标记为失效', async () => {
    const user = userEvent.setup()
    const queryClient = new QueryClient({
      defaultOptions: { queries: { retry: false, staleTime: 30_000 } },
    })
    // 模拟“刚打开过列表”的场景：缓存里已有一份新鲜的空列表。
    queryClient.setQueryData(TASK_LIST_KEY, { items: [], total: 0 })
    expect(queryClient.getQueryState(TASK_LIST_KEY)?.isInvalidated).toBe(false)

    render(
      <QueryClientProvider client={queryClient}>
        <MemoryRouter initialEntries={['/tasks/create?project_id=project-1']}>
          <TaskCreate />
        </MemoryRouter>
      </QueryClientProvider>
    )

    await user.type(await screen.findByLabelText('任务名称'), 'regression-task')
    await user.click(screen.getByRole('button', { name: /创建任务/ }))

    await waitFor(() => {
      expect(createTask).toHaveBeenCalledOnce()
    })
    await waitFor(() => {
      expect(queryClient.getQueryState(TASK_LIST_KEY)?.isInvalidated).toBe(true)
    })
  })
})
