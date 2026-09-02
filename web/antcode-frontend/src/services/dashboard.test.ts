import { beforeEach, describe, expect, it, vi } from 'vitest'

const mocks = vi.hoisted(() => ({ get: vi.fn() }))

vi.mock('./api', () => ({ default: { get: mocks.get } }))

import { dashboardService, systemHealthFromMetrics, type SystemMetrics } from './dashboard'

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
    const result = await dashboardService.getDashboardStats()

    expect(mocks.get).toHaveBeenCalledOnce()
    expect(mocks.get).toHaveBeenCalledWith('/api/v1/dashboard/summary', undefined)
    expect(result.projects).toEqual({ total: 600, active: 400, inactive: 200 })
    expect(result.tasks).toEqual({ total: 300, active: 40, running: 5, success: 240, failed: 20 })
  })

  it('loads the user summary without requiring admin system metrics', async () => {
    const result = await dashboardService.getDashboardStats()

    expect(mocks.get).toHaveBeenCalledOnce()
    expect(mocks.get).toHaveBeenCalledWith('/api/v1/dashboard/summary', undefined)
    // 摘要接口不带系统健康度。原先这里挂着一个恒为 'normal' 的 system 块，
    // 且本用例把它当正确结果钉住了——健康灯照着它读，于是 CPU 打满也报「健康」。
    expect(result).not.toHaveProperty('system')
  })
})

describe('systemHealthFromMetrics', () => {
  const withUsage = (cpu: number, memory: number, disk: number): SystemMetrics => ({
    ...metrics,
    cpu_usage: { percent: cpu, cores: 8 },
    memory_usage: { total: 16, used: 8, available: 8, percent: memory },
    disk_usage: { total: 500, used: 100, free: 400, percent: disk },
  })

  // 反例：拿不到指标（普通用户对 /dashboard/metrics 是 403）不能报「健康」。
  it('没有指标时是 unknown，不是 normal', () => {
    expect(systemHealthFromMetrics(null)).toBe('unknown')
  })

  it('按阈值分级', () => {
    expect(systemHealthFromMetrics(withUsage(10, 20, 30))).toBe('normal')
    expect(systemHealthFromMetrics(withUsage(71, 20, 30))).toBe('warning')
    expect(systemHealthFromMetrics(withUsage(10, 71, 30))).toBe('warning')
    expect(systemHealthFromMetrics(withUsage(10, 20, 86))).toBe('warning')
    expect(systemHealthFromMetrics(withUsage(91, 20, 30))).toBe('error')
    expect(systemHealthFromMetrics(withUsage(10, 91, 30))).toBe('error')
    expect(systemHealthFromMetrics(withUsage(10, 20, 96))).toBe('error')
  })
})
