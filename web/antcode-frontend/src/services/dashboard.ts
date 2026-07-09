import { BaseService } from './base'
import type { Project, Task } from '@/types'

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

export interface ProjectCount {
  total: number
  by_status: {
    active: number
    inactive: number
    archived: number
  }
  by_type: {
    file: number
    rule: number
    code: number
  }
}

export interface TaskSummary {
  total: number
  active: number
  running: number
  by_status: {
    pending: number
    running: number
    success: number
    failed: number
    paused: number
  }
}

class DashboardService extends BaseService {
  constructor() {
    super('/api/v1')
  }

  // 获取项目统计
  async getProjectStats(): Promise<ProjectCount> {
    const data = await this.get<{ items: Project[]; pagination: { total: number; page: number; size: number; pages: number } }>('/projects', {
      params: { page: 1, size: 1000 }
    })

    const { items: projects, pagination } = data
    const total = pagination.total

    const byStatus = projects.reduce<ProjectCount['by_status']>((acc, project) => {
      const status = project.status.toLowerCase() as keyof ProjectCount['by_status']
      if (!(status in acc)) {
        throw new Error(`未知项目状态: ${project.status}`)
      }
      acc[status] += 1
      return acc
    }, { active: 0, inactive: 0, archived: 0 })

    const byType = projects.reduce<ProjectCount['by_type']>((acc, project) => {
      const type = project.type.toLowerCase() as keyof ProjectCount['by_type']
      if (!(type in acc)) {
        throw new Error(`未知项目类型: ${project.type}`)
      }
      acc[type] += 1
      return acc
    }, { file: 0, rule: 0, code: 0 })

    return {
      total,
      by_status: byStatus,
      by_type: byType
    }
  }

  // 获取任务统计（对齐后端 /tasks 返回的 PaginationResponse 结构）
  async getTaskStats(): Promise<TaskSummary> {
    const data = await this.get<{ items: Task[]; pagination: { total: number; page: number; size: number; pages: number } }>('/tasks', {
      params: { page: 1, size: 1000 }
    })

    const { items: list, pagination } = data
    const total = pagination.total

    const active = list.filter((task: Task) => task.is_active).length
    const running = list.filter((task: Task) => task.status === 'running').length

    const byStatus = list.reduce<TaskSummary['by_status']>((acc, task) => {
      const status = task.status.toString().toLowerCase()
      switch (status) {
        case 'pending':
          acc.pending += 1
          break
        case 'running':
          acc.running += 1
          break
        case 'success':
          acc.success += 1
          break
        case 'failed':
          acc.failed += 1
          break
        case 'paused':
        case 'cancelled':
          acc.paused += 1
          break
        default:
          throw new Error(`未知任务状态: ${task.status}`)
      }
      return acc
    }, { pending: 0, running: 0, success: 0, failed: 0, paused: 0 })

    return { total, active, running, by_status: byStatus }
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

  // 获取完整的仪表板统计数据
  async getDashboardStats(): Promise<DashboardStats> {
    const [summary, systemMetrics] = await Promise.all([
      this.get<DashboardSummaryPayload>('/dashboard/summary'),
      this.getSystemMetrics()
    ])

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
        status: this.calculateSystemStatus(systemMetrics),
        uptime: systemMetrics.uptime,
        memory_usage: systemMetrics.memory_usage?.percent,
        cpu_usage: systemMetrics.cpu_usage?.percent,
        disk_usage: systemMetrics.disk_usage?.percent,
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
