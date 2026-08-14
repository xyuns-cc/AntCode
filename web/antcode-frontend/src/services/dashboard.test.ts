import { beforeEach, describe, expect, it, vi } from 'vitest'

const mocks = vi.hoisted(() => ({ get: vi.fn() }))

vi.mock('./api', () => ({ default: { get: mocks.get } }))

import { dashboardService, type SystemMetrics } from './dashboard'

const metrics: SystemMetrics = {
  active_tasks: 2,
  total_executions: 10,
  success_rate: 80,
  queue_size: 1,
  uptime: 3600,
}

describe('dashboardService', () => {
  beforeEach(() => {
    mocks.get.mockReset()
    mocks.get.mockResolvedValue({
      data: {
        data: {
          projects: { total: 600, by_status: { active: 400, inactive: 200 } },
          tasks: { total: 300, active: 40, running: 5, by_status: { success: 240, failed: 20 } },
        },
      },
    })
  })

  it('uses the server aggregate instead of oversized project and task pages', async () => {
    const result = await dashboardService.getDashboardStats(metrics)

    expect(mocks.get).toHaveBeenCalledOnce()
    expect(mocks.get).toHaveBeenCalledWith('/api/v1/dashboard/summary', undefined)
    expect(result.projects).toEqual({ total: 600, active: 400, inactive: 200 })
    expect(result.tasks).toEqual({ total: 300, active: 40, running: 5, success: 240, failed: 20 })
  })

  it('loads the user summary without requiring admin system metrics', async () => {
    const result = await dashboardService.getDashboardStats()

    expect(mocks.get).toHaveBeenCalledOnce()
    expect(mocks.get).toHaveBeenCalledWith('/api/v1/dashboard/summary', undefined)
    expect(result.system).toEqual({
      status: 'normal',
      uptime: 0,
      memory_usage: undefined,
      cpu_usage: undefined,
      disk_usage: undefined,
    })
  })
})
