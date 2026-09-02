/**
 * 钉住监控页确实走了 describeTaskWorkerBinding：光有 utils 那份用例的话，把 useTasks
 * 改回「拿 specified_worker_id 反查 Worker 列表」也不会有任何用例变红。
 */
import { renderHook, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { useTasks } from './hooks/useTasks'

const mocks = vi.hoisted(() => ({ getTasks: vi.fn(), getDashboardStats: vi.fn() }))

vi.mock('@/services/tasks', () => ({ taskService: { getTasks: mocks.getTasks } }))
vi.mock('@/services/dashboard', () => ({
  dashboardService: { getDashboardStats: mocks.getDashboardStats },
}))

describe('监控页任务表的 Worker 列显示的是绑定，不再把 auto 说成「未分配」', () => {
  beforeEach(() => {
    mocks.getDashboardStats.mockResolvedValue({
      tasks: { total: 2, active: 2, running: 2, success: 0, failed: 0 },
    })
  })

  it('auto 策略的运行中任务显示「自动选择」', async () => {
    mocks.getTasks.mockResolvedValue({
      items: [
        // 现场形状：auto 策略下 specified_worker_id 本来就是 null，任务照常在跑。
        { id: 't-1', name: 'auto-task', status: 'running', execution_strategy: 'auto', specified_worker_id: null },
        { id: 't-2', name: 'pinned-task', status: 'running', execution_strategy: 'specified', specified_worker_id: 'w-1', specified_worker_name: 'node-01' },
      ],
      total: 2,
      page: 1,
      size: 20,
    })

    const { result } = renderHook(() => useTasks(null))

    await waitFor(() => expect(result.current.tasks).toHaveLength(2))
    expect(result.current.tasks.map((task) => task.worker)).toEqual(['自动选择', 'node-01'])
  })

  it('拿不到 Worker 列表也不影响绑定文案 —— 它不再依赖 Worker 列表', async () => {
    mocks.getTasks.mockResolvedValue({
      items: [{ id: 't-1', name: 'auto-task', status: 'running', execution_strategy: 'auto' }],
      total: 1,
      page: 1,
      size: 20,
    })

    const { result } = renderHook(() => useTasks(undefined))

    await waitFor(() => expect(result.current.tasks).toHaveLength(1))
    expect(result.current.tasks[0].worker).toBe('自动选择')
    expect(result.current.tasks[0].worker).not.toBe('未分配')
  })
})
