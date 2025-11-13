import apiClient from './api'
import type { ApiResponse } from '@/types'

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
    completed: number
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
    completed: number
    failed: number
    paused: number
  }
}

class DashboardService {
  // 获取项目统计
  async getProjectStats(): Promise<ProjectCount> {
    try {
      // 获取项目列表来统计
      const response = await apiClient.get('/api/v1/projects', {
        params: { page: 1, size: 1000 } // 获取大量数据来统计
      })
      
      const projects = response.data.data || []
      const total = response.data.pagination?.total || 0
      
      // 统计各状态项目数量
      const byStatus = projects.reduce((acc: any, project: any) => {
        const status = project.status?.toLowerCase() || 'draft'
        acc[status] = (acc[status] || 0) + 1
        return acc
      }, { active: 0, inactive: 0, draft: 0, archived: 0 })

      // 统计各类型项目数量
      const byType = projects.reduce((acc: any, project: any) => {
        const type = project.type?.toLowerCase() || 'file'
        acc[type] = (acc[type] || 0) + 1
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

  // 获取任务统计（对齐后端 /scheduler/tasks 返回的 TaskListResponse 结构）
  async getTaskStats(): Promise<TaskSummary> {
    try {
      const response = await apiClient.get('/api/v1/scheduler/tasks', {
        params: { page: 1, size: 1000 }
      })

      // 后端返回结构: { total, page, size, items }
      const list = response.data?.items || []
      const total = response.data?.total ?? list.length

      const active = list.filter((task: any) => task.is_active).length
      const running = list.filter((task: any) => task.status === 'RUNNING').length

      const byStatus = list.reduce((acc: any, task: any) => {
        const status = (task.status || 'PENDING').toString().toLowerCase()
        acc[status] = (acc[status] || 0) + 1
        return acc
      }, { pending: 0, running: 0, completed: 0, failed: 0, paused: 0 })

      return { total, active, running, by_status: byStatus }
    } catch (error) {
      console.error('Failed to get task stats:', error)
      return {
        total: 0,
        active: 0,
        running: 0,
        by_status: { pending: 0, running: 0, completed: 0, failed: 0, paused: 0 }
      }
    }
  }

  // 获取系统指标（兼容后端精简的 SystemMetricsResponse）
  async getSystemMetrics(): Promise<SystemMetrics> {
    try {
      // 1) 核心系统指标（CPU/内存/磁盘/活跃任务）
      const sysResp = await apiClient.get('/api/v1/dashboard/metrics')
      const sysData = sysResp.data?.data || sysResp.data || {}

      // 2) 日志指标（用于 total_executions 等补充）
      let total_executions = 0
      try {
        const logResp = await apiClient.get('/api/v1/logs/metrics')
        total_executions = logResp.data?.data?.total_executions || 0
      } catch (e) {
        // 可忽略，不影响主流程
      }

      // 将后端字段映射为前端期望结构
      const mapped: SystemMetrics = {
        active_tasks: sysData.active_tasks ?? 0,
        total_executions,
        success_rate: 0, // 暂无整体成功率
        queue_size: 0,   // 暂无队列大小
        uptime: sysData.uptime_seconds ?? 0,
        memory_usage: sysData.memory_percent != null ? {
          total: sysData.memory_total ?? 0,
          used: sysData.memory_used ?? 0,
          available: sysData.memory_available ?? 0,
          percent: sysData.memory_percent
        } : undefined,
        cpu_usage: sysData.cpu_percent != null ? {
          percent: sysData.cpu_percent,
          cores: sysData.cpu_cores ?? ((navigator as any)?.hardwareConcurrency || 0)
        } : undefined,
        disk_usage: sysData.disk_usage != null ? {
          total: sysData.disk_total ?? 0,
          used: sysData.disk_used ?? 0,
          free: sysData.disk_free ?? 0,
          percent: sysData.disk_usage
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

  // 获取运行中的任务
  async getRunningTasks(): Promise<any[]> {
    try {
      const response = await apiClient.get<ApiResponse<any[]>>('/api/v1/scheduler/running')
      return response.data.data || []
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
          completed: summary.tasks?.by_status?.completed || 0,
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
        tasks: { total: 0, active: 0, running: 0, completed: 0, failed: 0 },
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
          cores: data.cpu_cores ?? ((navigator as any)?.hardwareConcurrency || 0)
        } : undefined,
        disk_usage: data.disk_usage != null ? {
          total: data.disk_total ?? 0,
          used: data.disk_used ?? 0,
          free: data.disk_free ?? 0,
          percent: data.disk_usage
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
