/**
 * Worker 服务 - 管理分布式 Worker 的 API 调用
 * 继承 BaseService 以复用统一的 HTTP 请求方法
 */
import { BaseService } from './base'
import { collectAllWorkers } from './workerPagination'
import type {
  BestWorkerResult,
  ClusterMetricHistory,
  WorkerConnectionTestResult,
  WorkerInstallKey,
  WorkerLoadRankingItem,
  WorkerMetricHistoryPoint,
  WorkerPermission,
  WorkerResourceDetails,
  WorkerResourceUpdate,
  WorkerResourceUpdateResult,
  WorkerUserPermission,
} from './workerServiceContracts'
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
  SpiderStatsHistoryPoint,
} from '@/types'

class WorkerService extends BaseService {
  constructor() {
    super('/api/v1/workers')
  }

  async getWorkers(
    params?: WorkerListParams,
    config?: AxiosRequestConfig
  ): Promise<WorkerListResponse> {
    return this.get<WorkerListResponse>('', {
      ...config,
      params: { ...(params ?? {}), ...(config?.params ?? {}) },
    })
  }

  async getAllWorkers(config?: AxiosRequestConfig): Promise<Worker[]> {
    return collectAllWorkers((page, size) =>
      this.get<WorkerListResponse>('', {
        ...config,
        params: { ...(config?.params ?? {}), page, size },
      })
    )
  }

  async getWorker(workerId: string): Promise<Worker> {
    return this.get<Worker>(`/${workerId}`)
  }

  async createWorker(data: WorkerCreateRequest): Promise<Worker> {
    return this.post<Worker>('', data)
  }

  async updateWorker(workerId: string, data: WorkerUpdateRequest): Promise<Worker> {
    return this.put<Worker>(`/${workerId}`, data)
  }

  async deleteWorker(workerId: string): Promise<void> {
    await this.delete(`/${workerId}`)
  }

  async batchDeleteWorkers(
    workerIds: string[]
  ): Promise<{ success_count: number; failed_count: number }> {
    return this.post<{ success_count: number; failed_count: number }>('/batch-delete', {
      worker_ids: workerIds,
    })
  }

  async getAggregateStats(config?: AxiosRequestConfig): Promise<WorkerAggregateStats> {
    return this.get<WorkerAggregateStats>('/stats', config)
  }

  async testConnection(workerId: string): Promise<WorkerConnectionTestResult> {
    return this.post<WorkerConnectionTestResult>(`/${workerId}/test`)
  }

  async generateInstallKey(osType: string, allowedSource?: string): Promise<WorkerInstallKey> {
    return this.post<WorkerInstallKey>('/generate-install-key', {
      os_type: osType,
      ...(allowedSource ? { allowed_source: allowedSource.trim() } : {}),
    })
  }

  async disconnectWorker(workerId: string): Promise<void> {
    await this.post(`/${workerId}/disconnect`)
  }

  async refreshWorkerStatus(workerId: string): Promise<Worker> {
    return this.post<Worker>(`/${workerId}/refresh`)
  }

  async getWorkerMetricsHistory(
    workerId: string,
    hours: number = 24,
    config?: AxiosRequestConfig
  ): Promise<WorkerMetricHistoryPoint[]> {
    const result = await this.get<WorkerMetricHistoryPoint[]>(`/${workerId}/metrics/history`, {
      ...config,
      params: { hours, ...(config?.params ?? {}) },
    })
    return result || []
  }

  async getClusterMetricsHistory(
    hours: number = 24,
    config?: AxiosRequestConfig
  ): Promise<ClusterMetricHistory> {
    const result = await this.get<ClusterMetricHistory>('/cluster/metrics/history', {
      ...config,
      params: { hours, ...(config?.params ?? {}) },
    })
    return (
      result || {
        timestamps: [],
        cpu: { avg: [], max: [], min: [] },
        memory: { avg: [], max: [], min: [] },
      }
    )
  }

  // ========== Worker 权限管理 ==========

  async getMyAvailableWorkers(config?: AxiosRequestConfig): Promise<Worker[]> {
    const result = await this.get<WorkerListResponse>('/my/available', config)
    return result?.items || []
  }

  async getWorkerUsers(workerId: string): Promise<WorkerUserPermission[]> {
    const result = await this.get<WorkerUserPermission[]>(`/${workerId}/users`)
    return result || []
  }

  async assignWorkerToUser(
    workerId: string,
    userId: string | number,
    permission: WorkerPermission = 'use',
    note?: string
  ): Promise<void> {
    await this.post(`/${workerId}/assign`, {
      user_id: userId,
      permission,
      note,
    })
  }

  async revokeWorkerFromUser(workerId: string, userId: string | number): Promise<void> {
    await this.delete(`/${workerId}/revoke/${userId}`)
  }

  async batchAssignWorkers(
    userId: string | number,
    workerIds: string[],
    permission: WorkerPermission = 'use'
  ): Promise<{
    success: number
    failed: number
  }> {
    return this.post<{ success: number; failed: number }>('/batch-assign', {
      user_id: userId,
      worker_ids: workerIds,
      permission,
    })
  }

  // ========== 负载均衡与分布式任务 ==========

  async getWorkersLoadRanking(params?: {
    region?: string
    top_n?: number
  }): Promise<WorkerLoadRankingItem[]> {
    const result = await this.get<WorkerLoadRankingItem[]>('/load/ranking', { params })
    return result || []
  }

  async getBestWorker(params?: { region?: string; tags?: string }): Promise<BestWorkerResult> {
    return this.get<BestWorkerResult>('/best', { params })
  }

  // ========== Worker 资源管理（管理员功能）==========

  async getWorkerResources(workerId: string): Promise<WorkerResourceDetails> {
    return this.get<WorkerResourceDetails>(`/${workerId}/resources`)
  }

  async updateWorkerResources(
    workerId: string,
    data: WorkerResourceUpdate
  ): Promise<WorkerResourceUpdateResult> {
    return this.post<WorkerResourceUpdateResult>(`/${workerId}/resources`, data)
  }

  // ========== 爬虫统计 ==========

  async getClusterSpiderStats(): Promise<ClusterSpiderStats> {
    return this.get<ClusterSpiderStats>('/stats/spider')
  }

  async getWorkerSpiderStats(workerId: string): Promise<SpiderStatsSummary> {
    return this.get<SpiderStatsSummary>(`/${workerId}/stats/spider`)
  }

  async getWorkerSpiderStatsHistory(
    workerId: string,
    hours: number = 24
  ): Promise<SpiderStatsHistoryPoint[]> {
    const result = await this.get<SpiderStatsHistoryPoint[]>(`/${workerId}/stats/spider/history`, {
      params: { hours },
    })
    return result || []
  }
}

export const workerService = new WorkerService()
export default workerService
