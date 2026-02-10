/**
 * 任务服务 - 管理任务调度的 API 调用
 */
import { BaseService } from './base'
import apiClient from './api'
import type {
  Task,
  TaskExecution,
  TaskCreateRequest,
  TaskUpdateRequest,
  TaskListParams,
  TaskListResponse,
  ApiResponse,
} from '@/types'

type PaginationPayload<T> = {
  data?: T[]
  pagination?: {
    total?: number
    page?: number
    size?: number
  }
}

class TaskService extends BaseService {
  constructor() {
    super('/api/v1/tasks')
  }

  private normalizeTaskListResponse(payload: unknown): TaskListResponse {
    const data = payload as Record<string, unknown> | null

    if (!data || typeof data !== 'object') {
      return { total: 0, page: 1, size: 20, items: [] }
    }

    if (Array.isArray(data.items)) {
      return {
        items: data.items as Task[],
        total: Number(data.total ?? (data.items as unknown[]).length) || 0,
        page: Number(data.page ?? 1) || 1,
        size: Number(data.size ?? 20) || 20,
      }
    }

    if (Array.isArray(data.data)) {
      const pagePayload = data as unknown as PaginationPayload<Task>
      const items = pagePayload.data || []
      return {
        items,
        total: Number(pagePayload.pagination?.total ?? items.length) || 0,
        page: Number(pagePayload.pagination?.page ?? 1) || 1,
        size: Number(pagePayload.pagination?.size ?? 20) || 20,
      }
    }

    if (data.data && typeof data.data === 'object') {
      const inner = data.data as Record<string, unknown>
      if (Array.isArray(inner.items)) {
        return {
          items: inner.items as Task[],
          total: Number(inner.total ?? (inner.items as unknown[]).length) || 0,
          page: Number(inner.page ?? 1) || 1,
          size: Number(inner.size ?? 20) || 20,
        }
      }
    }

    return { total: 0, page: 1, size: 20, items: [] }
  }

  private normalizeTaskExecutionsResponse(payload: unknown): {
    items: TaskExecution[]
    total: number
    page: number
    size: number
  } {
    const data = payload as Record<string, unknown> | null

    if (!data || typeof data !== 'object') {
      return { items: [], total: 0, page: 1, size: 20 }
    }

    if (Array.isArray(data.items)) {
      return {
        items: data.items as TaskExecution[],
        total: Number(data.total ?? (data.items as unknown[]).length) || 0,
        page: Number(data.page ?? 1) || 1,
        size: Number(data.size ?? 20) || 20,
      }
    }

    if (Array.isArray(data.data)) {
      const pagePayload = data as unknown as PaginationPayload<TaskExecution>
      const items = pagePayload.data || []
      return {
        items,
        total: Number(pagePayload.pagination?.total ?? items.length) || 0,
        page: Number(pagePayload.pagination?.page ?? 1) || 1,
        size: Number(pagePayload.pagination?.size ?? 20) || 20,
      }
    }

    if (data.data && typeof data.data === 'object') {
      const inner = data.data as Record<string, unknown>
      if (Array.isArray(inner.items)) {
        return {
          items: inner.items as TaskExecution[],
          total: Number(inner.total ?? (inner.items as unknown[]).length) || 0,
          page: Number(inner.page ?? 1) || 1,
          size: Number(inner.size ?? 20) || 20,
        }
      }
    }

    return { items: [], total: 0, page: 1, size: 20 }
  }

  // 获取任务列表
  async getTasks(params?: TaskListParams): Promise<TaskListResponse> {
    const response = await apiClient.get('/api/v1/tasks', { params })
    return this.normalizeTaskListResponse(response.data)
  }

  // 获取任务详情
  async getTask(id: string): Promise<Task> {
    return this.get<Task>(`/${id}`)
  }

  // 创建任务
  async createTask(data: TaskCreateRequest): Promise<Task> {
    return this.post<Task>('', data)
  }

  // 更新任务
  async updateTask(id: string, data: TaskUpdateRequest): Promise<Task> {
    return this.put<Task>(`/${id}`, data)
  }

  // 删除任务
  async deleteTask(id: string): Promise<void> {
    await this.delete(`/${id}`)
  }

  // 批量删除任务
  async batchDeleteTasks(ids: string[]): Promise<{
    success_count: number
    failed_count: number
    failed_ids: string[]
  }> {
    return this.post<{
      success_count: number
      failed_count: number
      failed_ids: string[]
    }>('/batch-delete', { task_ids: ids })
  }

  // 立即触发任务（保留 message 给页面展示）
  async triggerTask(id: string): Promise<ApiResponse<Record<string, unknown>>> {
    const response = await apiClient.post<ApiResponse<Record<string, unknown>>>(
      `/api/v1/tasks/${id}/trigger`,
      undefined,
      { headers: { 'X-Skip-Success-Toast': '1' } }
    )
    return response.data
  }

  // 取消任务执行
  async cancelTaskRun(runId: string): Promise<{ remote_cancelled: boolean }> {
    const response = await apiClient.post<ApiResponse<{ remote_cancelled: boolean }>>(
      `/api/v1/runs/${runId}/cancel`
    )
    return response.data?.data || { remote_cancelled: false }
  }

  // 获取任务执行记录
  async getTaskRuns(
    taskId: string,
    params?: {
      page?: number
      size?: number
      status?: string
    }
  ): Promise<{ items: TaskExecution[]; total: number; page: number; size: number }> {
    const response = await apiClient.get(`/api/v1/tasks/${taskId}/runs`, { params })
    return this.normalizeTaskExecutionsResponse(response.data)
  }

  // 获取任务执行详情
  async getTaskRun(runId: string): Promise<TaskExecution> {
    const response = await apiClient.get(`/api/v1/runs/${runId}`)
    const payload = response.data as { data?: TaskExecution }
    return payload?.data || (response.data as TaskExecution)
  }
}

export const taskService = new TaskService()
export default taskService
