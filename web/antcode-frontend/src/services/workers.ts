/**
 * Worker 服务 - 管理分布式 Worker 的 API 调用
 * 继承 BaseService 以复用统一的 HTTP 请求方法
 */
import { BaseService } from './base'
import type { AxiosRequestConfig } from 'axios'
import type {
  Worker,
  WorkerCreateRequest,
  WorkerUpdateRequest,
  WorkerListParams,
  WorkerListResponse,
  WorkerAggregateStats,
  SpiderStatsSummary,
  ClusterSpiderStats,
  SpiderStatsHistoryPoint
} from '@/types'

class WorkerService extends BaseService {
  constructor() {
    super('/api/v1/workers')
  }

  /**
   * 获取 Worker 列表
   */
  async getWorkers(params?: WorkerListParams, config?: AxiosRequestConfig): Promise<WorkerListResponse> {
    return this.get<WorkerListResponse>('', {
      ...config,
      params: { ...(params ?? {}), ...(config?.params ?? {}) }
    })
  }

  /**
   * 获取所有 Worker（不分页，用于选择器）
   */
  async getAllWorkers(config?: AxiosRequestConfig): Promise<Worker[]> {
    const result = await this.get<WorkerListResponse>('', {
      ...config,
      params: { page: 1, size: 100, ...(config?.params ?? {}) }
    })
    return result?.items || []
  }

  /**
   * 获取 Worker 详情
   */
  async getWorker(workerId: string): Promise<Worker> {
    return this.get<Worker>(`/${workerId}`)
  }

  /**
   * 创建 Worker
   */
  async createWorker(data: WorkerCreateRequest): Promise<Worker> {
    return this.post<Worker>('', data)
  }

  /**
   * 更新 Worker
   */
  async updateWorker(workerId: string, data: WorkerUpdateRequest): Promise<Worker> {
    return this.put<Worker>(`/${workerId}`, data)
  }

  /**
   * 删除 Worker
   */
  async deleteWorker(workerId: string): Promise<void> {
    await this.delete(`/${workerId}`)
  }

  /**
   * 批量删除 Worker
   */
  async batchDeleteWorkers(workerIds: string[]): Promise<{ success_count: number; failed_count: number }> {
    return this.post<{ success_count: number; failed_count: number }>(
      '/batch-delete',
      { worker_ids: workerIds }
    )
  }

  /**
   * 获取 Worker 聚合统计
   */
  async getAggregateStats(config?: AxiosRequestConfig): Promise<WorkerAggregateStats> {
    return this.get<WorkerAggregateStats>('/stats', config)
  }

  /**
   * 测试 Worker 连接
   */
  async testConnection(workerId: string): Promise<{ success: boolean; latency?: number; error?: string }> {
    return this.post<{ success: boolean; latency?: number; error?: string }>(`/${workerId}/test`)
  }

  /**
   * 生成 Worker 安装 Key
   * 返回包含安装命令的 Key 信息
   */
  async generateInstallKey(osType: string, allowedSource?: string): Promise<{
    key: string
    os_type: string
    allowed_source?: string
    install_command: string
    expires_at: string
  }> {
    return this.post<{
      key: string
      os_type: string
      allowed_source?: string
      install_command: string
      expires_at: string
    }>('/generate-install-key', {
      os_type: osType,
      ...(allowedSource ? { allowed_source: allowedSource.trim() } : {})
    })
  }


  /**
   * 获取 Worker 凭证（用于配置 Worker）
   */
  async getWorkerCredentials(workerId: string): Promise<{
    worker_id: string
    api_key: string
    secret_key: string
    gateway_host: string
    gateway_port: number
    transport_mode: string
    redis_url: string
    config_example: string
  }> {
    return this.get<{
      worker_id: string
      api_key: string
      secret_key: string
      gateway_host: string
      gateway_port: number
      transport_mode: string
      redis_url: string
      config_example: string
    }>(`/${workerId}/credentials`)
  }

  /**
   * 断开 Worker 连接
   */
  async disconnectWorker(workerId: string): Promise<void> {
    await this.post(`/${workerId}/disconnect`)
  }

  /**
   * 刷新 Worker 状态
   */
  async refreshWorkerStatus(workerId: string): Promise<Worker> {
    return this.post<Worker>(`/${workerId}/refresh`)
  }

  /**
   * 获取 Worker 历史指标
   */
  async getWorkerMetricsHistory(workerId: string, hours: number = 24, config?: AxiosRequestConfig): Promise<Array<{
    timestamp: string
    cpu: number
    memory: number
    disk: number
    taskCount: number
    runningTasks: number
    uptime: number
  }>> {
    const result = await this.get<Array<{
      timestamp: string
      cpu: number
      memory: number
      disk: number
      taskCount: number
      runningTasks: number
      uptime: number
    }>>(`/${workerId}/metrics/history`, {
      ...config,
      params: { hours, ...(config?.params ?? {}) }
    })
    return result || []
  }

  /**
   * 获取集群历史指标
   */
  async getClusterMetricsHistory(hours: number = 24, config?: AxiosRequestConfig): Promise<{
    timestamps: string[]
    cpu: { avg: number[]; max: number[]; min: number[] }
    memory: { avg: number[]; max: number[]; min: number[] }
  }> {
    const result = await this.get<{
      timestamps: string[]
      cpu: { avg: number[]; max: number[]; min: number[] }
      memory: { avg: number[]; max: number[]; min: number[] }
    }>('/cluster/metrics/history', {
      ...config,
      params: { hours, ...(config?.params ?? {}) }
    })
    return result || { timestamps: [], cpu: { avg: [], max: [], min: [] }, memory: { avg: [], max: [], min: [] } }
  }

  // ========== Worker 权限管理 ==========

  /**
   * 获取当前用户可用的 Worker 列表
   */
  async getMyAvailableWorkers(config?: AxiosRequestConfig): Promise<Worker[]> {
    const result = await this.get<WorkerListResponse>('/my/available', config)
    return result?.items || []
  }

  /**
   * 获取 Worker 的授权用户列表
   */
  async getWorkerUsers(workerId: string): Promise<Array<{
    user_id: string
    username: string
    permission: string
    assigned_at: string
    note?: string
  }>> {
    const result = await this.get<Array<{
      user_id: string
      username: string
      permission: string
      assigned_at: string
      note?: string
    }>>(`/${workerId}/users`)
    return result || []
  }

  /**
   * 分配 Worker 权限给用户
   */
  async assignWorkerToUser(workerId: string, userId: string | number, permission: string = 'use', note?: string): Promise<void> {
    await this.post(`/${workerId}/assign`, {
      user_id: userId,
      permission,
      note
    })
  }

  /**
   * 撤销用户的 Worker 权限
   */
  async revokeWorkerFromUser(workerId: string, userId: string | number): Promise<void> {
    await this.delete(`/${workerId}/revoke/${userId}`)
  }

  /**
   * 批量分配 Worker 权限
   */
  async batchAssignWorkers(userId: string | number, workerIds: string[], permission: string = 'use'): Promise<{
    success: number
    failed: number
  }> {
    return this.post<{ success: number; failed: number }>('/batch-assign', {
      user_id: userId,
      worker_ids: workerIds,
      permission
    })
  }

  // ========== 负载均衡与分布式任务 ==========

  /**
   * 获取 Worker 负载排名
   */
  async getWorkersLoadRanking(params?: {
    region?: string
    top_n?: number
  }): Promise<Array<{
    worker_id: string
    name: string
    status: string
    load_score: number
    available: boolean
    latency_ms: number
    metrics: {
      cpu: number
      memory: number
      runningTasks: number
      maxConcurrentTasks: number
    }
  }>> {
    const result = await this.get<Array<{
      worker_id: string
      name: string
      status: string
      load_score: number
      available: boolean
      latency_ms: number
      metrics: {
        cpu: number
        memory: number
        runningTasks: number
        maxConcurrentTasks: number
      }
    }>>('/load/ranking', { params })
    return result || []
  }

  /**
   * 获取最佳 Worker（负载最低的可用 Worker）
   */
  async getBestWorker(params?: {
    region?: string
    tags?: string
    require_render?: boolean
  }): Promise<{
    available: boolean
    worker?: Worker
    load_score?: number
    message?: string
  }> {
    return this.get<{
      available: boolean
      worker?: Worker
      load_score?: number
      message?: string
    }>('/best', { params })
  }

  /**
   * 分发任务到 Worker
   */
  async dispatchTask(data: {
    project_id: string
    params?: Record<string, unknown>
    environment_vars?: Record<string, string>
    timeout?: number
    worker_id?: string
    region?: string
    tags?: string[]
    priority?: number
    project_type?: string
    require_render?: boolean
  }): Promise<{
    success: boolean
    run_id?: string
    worker_id?: string
    worker_name?: string
    message?: string
  }> {
    return this.post<{
      success: boolean
      run_id?: string
      worker_id?: string
      worker_name?: string
      message?: string
    }>('/dispatch/task', data)
  }

  /**
   * 获取分布式任务日志
   */
  async getDistributedLogs(runId: string, params?: {
    log_type?: 'stdout' | 'stderr'
    tail?: number
  }): Promise<{
    run_id: string
    log_type: string
    logs: string[]
    total: number
  }> {
    return this.get<{
      run_id: string
      log_type: string
      logs: string[]
      total: number
    }>(`/distributed-logs/${runId}`, { params })
  }

  /**
   * 获取远程 Worker 任务状态
   */
  async getRemoteTaskStatus(workerId: string, taskId: string): Promise<{
    status: string
    exit_code?: number
    error_message?: string
  }> {
    return this.get<{
      status: string
      exit_code?: number
      error_message?: string
    }>(`/dispatch/task/${workerId}/${taskId}/status`)
  }

  /**
   * 获取远程 Worker 任务日志
   */
  async getRemoteTaskLogs(workerId: string, taskId: string, params?: {
    tail?: number
  }): Promise<{
    logs: string[]
    total: number
  }> {
    return this.get<{
      logs: string[]
      total: number
    }>(`/dispatch/task/${workerId}/${taskId}/logs`, { params })
  }

  // ========== Worker 资源管理（管理员功能）==========

  /**
   * 获取 Worker 资源限制（管理员可查看）
   */
  async getWorkerResources(workerId: string): Promise<{
    limits: {
      max_concurrent_tasks: number
      task_memory_limit_mb: number
      task_cpu_time_limit_sec: number
      task_timeout?: number
    }
    auto_adjustment: boolean
    resource_stats: {
      cpu_percent: number
      memory_percent: number
      disk_percent: number
      memory_used_mb: number
      memory_total_mb: number
      disk_used_gb: number
      disk_total_gb: number
      running_tasks: number
      queued_tasks: number
      uptime_seconds: number
    }
  }> {
    return this.get<{
      limits: {
        max_concurrent_tasks: number
        task_memory_limit_mb: number
        task_cpu_time_limit_sec: number
        task_timeout?: number
      }
      auto_adjustment: boolean
      resource_stats: {
        cpu_percent: number
        memory_percent: number
        disk_percent: number
        memory_used_mb: number
        memory_total_mb: number
        disk_used_gb: number
        disk_total_gb: number
        running_tasks: number
        queued_tasks: number
        uptime_seconds: number
      }
    }>(`/${workerId}/resources`)
  }

  /**
   * 调整 Worker 资源限制（仅超级管理员）
   */
  async updateWorkerResources(workerId: string, data: {
    max_concurrent_tasks?: number
    task_memory_limit_mb?: number
    task_cpu_time_limit_sec?: number
    auto_resource_limit?: boolean
  }): Promise<{
    success: boolean
    updated: Record<string, unknown>
    current_limits: {
      max_concurrent_tasks: number
      task_memory_limit_mb: number
      task_cpu_time_limit_sec: number
      auto_resource_limit: boolean
    }
  }> {
    return this.post<{
      success: boolean
      updated: Record<string, unknown>
      current_limits: {
        max_concurrent_tasks: number
        task_memory_limit_mb: number
        task_cpu_time_limit_sec: number
        auto_resource_limit: boolean
      }
    }>(`/${workerId}/resources`, data)
  }

  // ========== 爬虫统计 ==========

  /**
   * 获取集群爬虫统计
   */
  async getClusterSpiderStats(): Promise<ClusterSpiderStats> {
    return this.get<ClusterSpiderStats>('/stats/spider')
  }

  /**
   * 获取单 Worker 爬虫统计
   */
  async getWorkerSpiderStats(workerId: string): Promise<SpiderStatsSummary> {
    return this.get<SpiderStatsSummary>(`/${workerId}/stats/spider`)
  }

  /**
   * 获取 Worker 爬虫统计历史
   */
  async getWorkerSpiderStatsHistory(workerId: string, hours: number = 24): Promise<SpiderStatsHistoryPoint[]> {
    const result = await this.get<SpiderStatsHistoryPoint[]>(`/${workerId}/stats/spider/history`, { params: { hours } })
    return result || []
  }
}

export const workerService = new WorkerService()
export default workerService
