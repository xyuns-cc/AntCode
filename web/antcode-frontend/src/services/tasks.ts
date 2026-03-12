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
  PaginationResponse,
} from '@/types'

class TaskService extends BaseService {
  constructor() {
    super('/api/v1/tasks')
  }

  // 获取任务列表
  async getTasks(params?: TaskListParams): Promise<TaskListResponse> {
    const response = await apiClient.get<PaginationResponse<Task>>('/api/v1/tasks', { params })
    const { items, pagination } = response.data.data

    return {
      items,
      total: pagination.total,
      page: pagination.page,
      size: pagination.size,
    }
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
    return response.data.data
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
    const response = await apiClient.get<PaginationResponse<TaskExecution>>(`/api/v1/tasks/${taskId}/runs`, { params })
    const { items, pagination } = response.data.data

    return {
      items,
      total: pagination.total,
      page: pagination.page,
      size: pagination.size,
    }
  }

  // 获取任务执行详情
  async getTaskRun(runId: string): Promise<TaskExecution> {
    const response = await apiClient.get<ApiResponse<TaskExecution>>(`/api/v1/runs/${runId}`)
    return response.data.data
  }
}

export const taskService = new TaskService()
export default taskService
