/**
 * 任务服务 - 管理任务调度的 API 调用
 * 继承 BaseService 以复用统一的 HTTP 请求方法
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
  ApiResponse
} from '@/types'

class TaskService extends BaseService {
  constructor() {
    super('/api/v1/scheduler')
  }

  // 获取任务列表
  async getTasks(params?: TaskListParams): Promise<TaskListResponse> {
    // 任务列表接口返回格式特殊，直接使用 apiClient
    const response = await apiClient.get<TaskListResponse>('/api/v1/scheduler/tasks', { params })
    return response.data
  }

  // 获取任务详情
  async getTask(id: string): Promise<Task> {
    return this.get<Task>(`/tasks/${id}`)
  }

  // 创建任务
  async createTask(data: TaskCreateRequest): Promise<Task> {
    return this.post<Task>('/tasks', data)
  }

  // 更新任务
  async updateTask(id: string, data: TaskUpdateRequest): Promise<Task> {
    return this.put<Task>(`/tasks/${id}`, data)
  }

  // 删除任务
  async deleteTask(id: string): Promise<void> {
    await this.delete(`/tasks/${id}`)
  }

  // 批量删除任务
  async batchDeleteTasks(ids: string[]): Promise<{ success_count: number; failed_count: number; failed_ids: string[] }> {
    return this.post<{ success_count: number; failed_count: number; failed_ids: string[] }>(
      '/tasks/batch-delete',
      { task_ids: ids }
    )
  }


  // 立即触发任务
  async triggerTask(id: string): Promise<ApiResponse<Record<string, unknown>>> {
    const response = await apiClient.post<ApiResponse<Record<string, unknown>>>(
      `/api/v1/scheduler/tasks/${id}/trigger`,
      undefined,
      { headers: { 'X-Skip-Success-Toast': '1' } }
    )
    return response.data
  }

  // 取消任务执行
  async cancelTaskExecution(executionId: string): Promise<{ remote_cancelled: boolean }> {
    return this.post<{ remote_cancelled: boolean }>(`/executions/${executionId}/cancel`)
  }

  // 获取任务执行记录
  async getTaskExecutions(taskId: string, params?: {
    page?: number
    size?: number
    status?: string
  }): Promise<{ items: TaskExecution[]; total: number; page: number; size: number }> {
    const response = await apiClient.get<{ items: TaskExecution[]; total: number; page: number; size: number }>(
      `/api/v1/scheduler/tasks/${taskId}/executions`,
      { params }
    )
    return response.data
  }

  // 获取任务执行详情
  async getTaskExecution(executionId: string): Promise<TaskExecution> {
    return this.get<TaskExecution>(`/executions/${executionId}`)
  }
}

export const taskService = new TaskService()
export default taskService
