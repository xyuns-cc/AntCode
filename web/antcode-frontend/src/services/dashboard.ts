import { BaseService } from './base'
import type { Task } from '@/types'

interface DashboardMetricsPayload {
  active_tasks?: number
  success_rate?: number
  queue_size?: number
  uptime_seconds?: number
  memory_total?: number
  memory_used?: number
  memory_available?: number
  memory_percent?: number
  cpu_percent?: number
  cpu_cores?: number
  disk_total?: number
  disk_used?: number
  disk_free?: number
  disk_percent?: number
}

interface DashboardSummaryPayload {
  projects?: {
    total?: number
    by_status?: {
      active?: number
      inactive?: number
    }
  }
  tasks?: {
    total?: number
    active?: number
    running?: number
    by_status?: {
      success?: number
      failed?: number
    }
  }
}

export interface DashboardStats {
  projects: {
    total: number
    active: number
    inactive: number
  }
  tasks: {
    total: number
    active: number
    running: number
    success: number
    failed: number
  }
  system: {
    status: 'normal' | 'warning' | 'error'
    uptime: number
    memory_usage?: number
    cpu_usage?: number
    disk_usage?: number
  }
}

export interface SystemMetrics {
  active_tasks: number
  total_executions: number
  success_rate: number
  queue_size: number
  memory_usage?: {
    total: number
    used: number
    available: number
    percent: number
  }
  cpu_usage?: {
    percent: number
    cores: number
  }
  disk_usage?: {
    total: number
    used: number
    free: number
    percent: number
  }
  uptime: number
}

// 24小时任务趋势数据
export interface HourlyTrendItem {
  hour: number
  tasks: number
  success: number
  failed: number
}

class DashboardService extends BaseService {
  constructor() {
    super('/api/v1')
  }

  // 获取系统指标
  async getSystemMetrics(): Promise<SystemMetrics> {
    const sysData = await this.get<DashboardMetricsPayload>('/dashboard/metrics')
    const logData = await this.get<{ total_executions: number }>('/logs/metrics')

    const hardwareConcurrency = typeof navigator !== 'undefined'
      ? navigator.hardwareConcurrency ?? 0
      : 0

    return {
      active_tasks: sysData.active_tasks ?? 0,
      total_executions: logData.total_executions,
      success_rate: sysData.success_rate ?? 0,
      queue_size: sysData.queue_size ?? 0,
      uptime: sysData.uptime_seconds ?? 0,
      memory_usage: sysData.memory_percent != null ? {
        total: sysData.memory_total ?? 0,
        used: sysData.memory_used ?? 0,
        available: sysData.memory_available ?? 0,
        percent: sysData.memory_percent
      } : undefined,
      cpu_usage: sysData.cpu_percent != null ? {
        percent: sysData.cpu_percent,
        cores: sysData.cpu_cores ?? hardwareConcurrency
      } : undefined,
      disk_usage: sysData.disk_percent != null ? {
        total: sysData.disk_total ?? 0,
        used: sysData.disk_used ?? 0,
        free: sysData.disk_free ?? 0,
        percent: sysData.disk_percent
      } : undefined
    }
  }

  // 获取24小时任务趋势数据
  async getHourlyTrend(): Promise<HourlyTrendItem[]> {
    return await this.get<HourlyTrendItem[]>('/dashboard/tasks/hourly-trend')
  }

  // 获取运行中的任务
  async getRunningTasks(): Promise<Task[]> {
    return await this.get<Task[]>('/tasks/running')
  }

  // 摘要对所有登录用户可用；管理员系统指标是可选增强，不能阻断摘要。
  async getDashboardStats(metrics?: SystemMetrics): Promise<DashboardStats> {
    const summary = await this.get<DashboardSummaryPayload>('/dashboard/summary')

    return {
      projects: {
        total: summary.projects?.total ?? 0,
        active: summary.projects?.by_status?.active ?? 0,
        inactive: summary.projects?.by_status?.inactive ?? 0,
      },
      tasks: {
        total: summary.tasks?.total ?? 0,
        active: summary.tasks?.active ?? 0,
        running: summary.tasks?.running ?? 0,
        success: summary.tasks?.by_status?.success ?? 0,
        failed: summary.tasks?.by_status?.failed ?? 0,
      },
      system: {
        status: metrics ? this.calculateSystemStatus(metrics) : 'normal',
        uptime: metrics?.uptime ?? 0,
        memory_usage: metrics?.memory_usage?.percent,
        cpu_usage: metrics?.cpu_usage?.percent,
        disk_usage: metrics?.disk_usage?.percent,
      },
    }
  }

  // 计算系统状态
  private calculateSystemStatus(metrics: SystemMetrics): 'normal' | 'warning' | 'error' {
    // 基于CPU、内存和磁盘使用率判断系统状态
    const cpuUsage = metrics.cpu_usage?.percent || 0
    const memoryUsage = metrics.memory_usage?.percent || 0
    const diskUsage = metrics.disk_usage?.percent || 0

    if (cpuUsage > 90 || memoryUsage > 90 || diskUsage > 95) {
      return 'error'
    } else if (cpuUsage > 70 || memoryUsage > 70 || diskUsage > 85) {
      return 'warning'
    }

    return 'normal'
  }

  // 刷新系统指标缓存（同样做字段映射）
  async refreshSystemMetrics(): Promise<SystemMetrics> {
    try {
      const data = await this.post<DashboardMetricsPayload>('/dashboard/metrics/refresh')

      const hardwareConcurrency = typeof navigator !== 'undefined'
        ? navigator.hardwareConcurrency ?? 0
        : 0

      const mapped: SystemMetrics = {
        active_tasks: data.active_tasks ?? 0,
        total_executions: 0,
        success_rate: 0,
        queue_size: 0,
        uptime: data.uptime_seconds ?? 0,
        memory_usage: data.memory_percent != null ? {
          total: data.memory_total ?? 0,
          used: data.memory_used ?? 0,
          available: data.memory_available ?? 0,
          percent: data.memory_percent
        } : undefined,
        cpu_usage: data.cpu_percent != null ? {
          percent: data.cpu_percent,
          cores: data.cpu_cores ?? hardwareConcurrency
        } : undefined,
        disk_usage: data.disk_percent != null ? {
          total: data.disk_total ?? 0,
          used: data.disk_used ?? 0,
          free: data.disk_free ?? 0,
          percent: data.disk_percent
        } : undefined,
      }

      return mapped
    } catch (error) {
      console.error('Failed to refresh system metrics:', error)
      throw error
    }
  }
}

export const dashboardService = new DashboardService()
export default dashboardService
