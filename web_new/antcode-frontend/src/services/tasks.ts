/**
 * 任务服务 - 管理任务调度的 API 调用
 * 继承 BaseService 以复用统一的 HTTP 请求方法
 */
import { BaseService } from './base'
import apiClient from './api'
import type {
  Task,
  TaskRun,
  TaskCreateRequest,
  TaskUpdateRequest,
  TaskListParams,
  TaskListResponse,
  TaskStats,
} from '@/types'

class TaskService extends BaseService {
  constructor() {
    super('/api/v1/tasks')
  }

  // 获取任务列表
  async getTasks(params?: TaskListParams): Promise<TaskListResponse> {
    return this.get<TaskListResponse>('', { params })
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
  async batchDeleteTasks(ids: (string | number)[]): Promise<{ success_count: number; failed_count: number; failed_ids: (string | number)[] }> {
    return this.post<{ success_count: number; failed_count: number; failed_ids: (string | number)[] }>(
      '/batch-delete',
      { task_ids: ids }
    )
  }

  // 立即触发任务
  async triggerTask(id: string): Promise<Record<string, unknown>> {
    return this.post<Record<string, unknown>>(`/${id}/trigger`)
  }

  // 获取任务运行记录
  async getTaskRuns(taskId: string, params?: {
    page?: number
    size?: number
    status?: string
  }): Promise<{ items: TaskRun[]; total: number }> {
    const response = await this.get<{ items: TaskRun[]; total: number; page: number; size: number }>(
      `/${taskId}/runs`,
      { params }
    )
    return { items: response.items || [], total: response.total || 0 }
  }

  // 取消任务运行
  async cancelTaskRun(runId: string): Promise<{ remote_cancelled: boolean; execution_id?: string; status?: string }> {
    const response = await apiClient.post<{ remote_cancelled: boolean; execution_id?: string; status?: string }>(
      `/api/v1/runs/${runId}/cancel`
    )
    return response.data
  }

  // 获取任务统计信息
  async getTaskStats(taskId: string): Promise<TaskStats> {
    return this.get<TaskStats>(`/${taskId}/stats`)
  }
}

export const taskService = new TaskService()
export default taskService
