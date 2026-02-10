import apiClient from './api'
import type { ApiResponse, PaginationResponse, Project, Task } from '@/types'

type ProjectListResponse = ApiResponse<Project[]> & {
  pagination?: PaginationResponse<Project>['pagination']
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
    draft: number
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

class DashboardService {
  // 获取项目统计
  async getProjectStats(): Promise<ProjectCount> {
    try {
      // 获取项目列表来统计
      const response = await apiClient.get<ProjectListResponse>('/api/v1/projects', {
        params: { page: 1, size: 1000 } // 获取大量数据来统计
      })
      
      const projects = response.data.data || []
      const total = response.data.pagination?.total || projects.length
      
      // 统计各状态项目数量
      const byStatus = projects.reduce<ProjectCount['by_status']>((acc, project) => {
        const status = (project.status ?? 'draft').toLowerCase() as keyof ProjectCount['by_status']
        if (status in acc) {
          acc[status] += 1
        } else {
          acc.draft += 1
        }
        return acc
      }, { active: 0, inactive: 0, draft: 0, archived: 0 })

      // 统计各类型项目数量
      const byType = projects.reduce<ProjectCount['by_type']>((acc, project) => {
        const type = (project.type ?? 'file').toLowerCase() as keyof ProjectCount['by_type']
        if (type in acc) {
          acc[type] += 1
        } else {
          acc.file += 1
        }
        return acc
      }, { file: 0, rule: 0, code: 0 })

      return {
        total,
        by_status: byStatus,
        by_type: byType
      }
    } catch (error) {
      console.error('Failed to get project stats:', error)
      // 返回默认值
      return {
        total: 0,
        by_status: { active: 0, inactive: 0, draft: 0, archived: 0 },
        by_type: { file: 0, rule: 0, code: 0 }
      }
    }
  }

  // 获取任务统计（对齐后端 /tasks 返回的 PaginationResponse 结构）
  async getTaskStats(): Promise<TaskSummary> {
    try {
      const response = await apiClient.get<PaginationResponse<Task>>('/api/v1/tasks', {
        params: { page: 1, size: 1000 }
      })

      // 后端返回结构: { success, data: Task[], pagination }
      const list = response.data?.data ?? []
      const total = response.data?.pagination?.total ?? list.length

      const active = list.filter((task: Task) => task.is_active).length
      const running = list.filter((task: Task) => task.status === 'running').length

      const byStatus = list.reduce<TaskSummary['by_status']>((acc, task) => {
        const status = (task.status ?? 'pending').toString().toLowerCase()
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
          case 'error':
            acc.failed += 1
            break
          case 'paused':
          case 'cancelled':
            acc.paused += 1
            break
          default:
            acc.pending += 1
        }
        return acc
      }, { pending: 0, running: 0, success: 0, failed: 0, paused: 0 })

      return { total, active, running, by_status: byStatus }
    } catch (error) {
      console.error('Failed to get task stats:', error)
      return {
        total: 0,
        active: 0,
        running: 0,
        by_status: { pending: 0, running: 0, success: 0, failed: 0, paused: 0 }
      }
    }
  }

  // 获取系统指标
  async getSystemMetrics(): Promise<SystemMetrics> {
    try {
      // 1) 核心系统指标（CPU/内存/磁盘/活跃任务/队列大小/成功率）
      const sysResp = await apiClient.get('/api/v1/dashboard/metrics')
      const sysData = sysResp.data?.data || sysResp.data || {}

      // 2) 日志指标（用于 total_executions 等补充）
      let total_executions = 0
      try {
        const logResp = await apiClient.get('/api/v1/logs/metrics')
        total_executions = logResp.data?.data?.total_executions || 0
      } catch (err) {
        console.warn('Failed to get log metrics', err)
      }

      const hardwareConcurrency = typeof navigator !== 'undefined'
        ? navigator.hardwareConcurrency ?? 0
        : 0

      // 将后端字段映射为前端期望结构
      const mapped: SystemMetrics = {
        active_tasks: sysData.active_tasks ?? 0,
        total_executions,
        success_rate: sysData.success_rate ?? 0,  // 使用后端返回的成功率
        queue_size: sysData.queue_size ?? 0,      // 使用后端返回的队列大小
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
        disk_usage: sysData.disk_percent != null ? {  // 修复：使用 disk_percent
          total: sysData.disk_total ?? 0,
          used: sysData.disk_used ?? 0,
          free: sysData.disk_free ?? 0,
          percent: sysData.disk_percent  // 修复：使用 disk_percent
        } : undefined
      }

      return mapped
    } catch (error) {
      console.error('Failed to get system metrics:', error)
      return {
        active_tasks: 0,
        total_executions: 0,
        success_rate: 0,
        queue_size: 0,
        uptime: 0
      }
    }
  }

  // 获取24小时任务趋势数据
  async getHourlyTrend(): Promise<HourlyTrendItem[]> {
    try {
      const response = await apiClient.get<ApiResponse<HourlyTrendItem[]>>('/api/v1/dashboard/tasks/hourly-trend')
      return response.data.data ?? []
    } catch (error) {
      console.error('Failed to get hourly trend:', error)
      // 返回空数组，前端会显示空状态
      return []
    }
  }

  // 获取运行中的任务
  async getRunningTasks(): Promise<Task[]> {
    try {
      const response = await apiClient.get<ApiResponse<Task[]>>('/api/v1/tasks/running')
      return response.data.data ?? []
    } catch (error) {
      console.error('Failed to get running tasks:', error)
      return []
    }
  }

  // 获取完整的仪表板统计数据
  async getDashboardStats(): Promise<DashboardStats> {
    try {
      // 优先使用后端提供的全量汇总统计
      const [summaryResp, systemMetrics] = await Promise.all([
        apiClient.get('/api/v1/dashboard/summary'),
        this.getSystemMetrics()
      ])
      const summary = summaryResp.data?.data || summaryResp.data || {}

      return {
        projects: {
          total: summary.projects?.total || 0,
          active: summary.projects?.by_status?.active || 0,
          inactive: summary.projects?.by_status?.inactive || 0,
        },
        tasks: {
          total: summary.tasks?.total || 0,
          active: summary.tasks?.active || 0,
          running: summary.tasks?.running || 0,
          success: summary.tasks?.by_status?.success || 0,
          failed: summary.tasks?.by_status?.failed || 0,
        },
        system: {
          status: this.calculateSystemStatus(systemMetrics),
          uptime: systemMetrics.uptime,
          memory_usage: systemMetrics.memory_usage?.percent,
          cpu_usage: systemMetrics.cpu_usage?.percent,
          disk_usage: systemMetrics.disk_usage?.percent,
        },
      }
    } catch (error) {
      console.error('Failed to get dashboard stats:', error)
      // 返回默认值
      return {
        projects: { total: 0, active: 0, inactive: 0 },
        tasks: { total: 0, active: 0, running: 0, success: 0, failed: 0 },
        system: { status: 'error', uptime: 0 }
      }
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
      const resp = await apiClient.post('/api/v1/dashboard/metrics/refresh')
      const data = resp.data?.data || resp.data || {}

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
        disk_usage: data.disk_percent != null ? {  // 修复：使用 disk_percent
          total: data.disk_total ?? 0,
          used: data.disk_used ?? 0,
          free: data.disk_free ?? 0,
          percent: data.disk_percent  // 修复：使用 disk_percent
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
