import type { AxiosError } from 'axios'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import type { Task, TaskExecution, Worker } from '@/types'
import { presentApiError } from '@/utils/apiErrorPresentation'

const apiMocks = vi.hoisted(() => ({
  get: vi.fn(),
  post: vi.fn(),
  put: vi.fn(),
  patch: vi.fn(),
  delete: vi.fn(),
}))

vi.mock('./api', () => ({ default: apiMocks }))

import { taskService } from './tasks'
import { workerService } from './workers'

const task: Task = {
  id: 'task-1',
  name: 'daily-crawl',
  project_id: 'project-1',
  task_type: 'spider',
  schedule_type: 'cron',
  status: 'pending',
  is_active: true,
  created_at: '2026-07-13T00:00:00Z',
  updated_at: '2026-07-13T00:00:00Z',
  created_by: 'user-1',
  created_by_username: 'alice',
}

const execution: TaskExecution = {
  id: 'execution-1',
  task_id: task.id,
  run_id: 'run-1',
  start_time: '2026-07-13T00:00:00Z',
  status: 'running',
}

const worker: Worker = {
  id: 'worker-1',
  name: 'worker-east',
  host: '10.0.0.5',
  port: 8001,
  status: 'online',
  region: 'east',
  lastHeartbeat: '2026-07-13T00:00:00Z',
  createdAt: '2026-07-13T00:00:00Z',
}

describe('frontend API contracts', () => {
  beforeEach(() => apiMocks.get.mockReset())

  it('maps the task pagination envelope without losing list fields', async () => {
    apiMocks.get.mockResolvedValue({
      data: { data: { items: [task], pagination: { total: 21, page: 2, size: 10 } } },
    })

    const result = await taskService.getTasks({ page: 2, size: 10 })

    expect(result).toEqual({ items: [task], total: 21, page: 2, size: 10 })
    expect(result.items[0]).toMatchObject({
      name: 'daily-crawl',
      status: 'pending',
      schedule_type: 'cron',
      created_by_username: 'alice',
    })
  })

  it('unwraps the worker response envelope and preserves operational fields', async () => {
    apiMocks.get.mockResolvedValue({
      data: { data: { items: [worker], total: 1, page: 1, size: 20 } },
    })

    const result = await workerService.getWorkers({ page: 1, size: 20 })

    expect(result.items[0]).toMatchObject({
      name: 'worker-east',
      status: 'online',
      region: 'east',
      lastHeartbeat: '2026-07-13T00:00:00Z',
    })
  })

  it('uses the legacy run endpoint only when the new endpoint returns 404', async () => {
    apiMocks.get
      .mockRejectedValueOnce({ response: { status: 404 } })
      .mockResolvedValueOnce({ data: { data: execution } })

    await expect(taskService.getTaskRun('run-1')).resolves.toEqual(execution)
    expect(apiMocks.get).toHaveBeenNthCalledWith(1, '/api/v1/runs/run-1')
    expect(apiMocks.get).toHaveBeenNthCalledWith(2, '/api/v1/tasks/runs/run-1')
  })

  it('does not hide non-404 failures behind the legacy endpoint', async () => {
    const error = { kind: 'service-unavailable', response: { status: 503 } }
    apiMocks.get.mockImplementationOnce(async () => {
      throw error
    })

    let caught: unknown
    try {
      await taskService.getTaskRun('run-1')
    } catch (requestError) {
      caught = requestError
    }

    expect(caught).toEqual(error)
    expect(apiMocks.get).toHaveBeenCalledOnce()
  })

  it.each([
    [{ message: '任务名称已存在' }, '任务名称已存在'],
    [{ detail: '登录凭据无效' }, '登录凭据无效'],
  ])('keeps backend error semantics for %o', (data, expectedTitle) => {
    const error = {
      message: 'Request failed',
      response: { status: 422, data },
    } as AxiosError<unknown>

    expect(presentApiError(error).title).toBe(expectedTitle)
  })
})
