/**
 * 任务服务 - 管理任务调度的 API 调用
 */
import type { AxiosError } from 'axios'
import { BaseService } from './base'
import apiClient from './api'
import type { BatchFailure } from './batchOutcome'
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

export interface BatchDeleteTasksResult {
  success_count: number
  failed_count: number
  failed_ids: string[]
  failures: BatchFailure[]
}

// triggerTask 返回的载荷（后端现已返回 task_id/run_id/triggered）
export interface TriggerTaskResponse {
  task_id?: string
  run_id?: string
  triggered?: boolean
  message?: string
  [key: string]: unknown
}

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

  // 批量删除任务。failures 逐项给出失败原因（如"仍由在线 Worker X 持有"），
  // failed_ids 是它的 id 投影，保留给只读 id 的旧调用方。
  async batchDeleteTasks(ids: string[]): Promise<BatchDeleteTasksResult> {
    return this.post<BatchDeleteTasksResult>('/batch-delete', { task_ids: ids })
  }

  // 立即触发任务（保留 message 给页面展示）
  // 返回 { task_id, run_id, triggered } —— run_id 可用于跳转日志页
  async triggerTask(id: string): Promise<TriggerTaskResponse> {
    const response = await apiClient.post<ApiResponse<TriggerTaskResponse>>(
      `/api/v1/tasks/${id}/trigger`,
      undefined,
      { headers: { 'X-Skip-Success-Toast': '1' } }
    )
    // 后端可能直接返回 { data: {...} } 也可能扁平返回，做容错
    const body = response.data
    if (body && typeof body === 'object' && 'data' in body && body.data) {
      return { ...(body.data as TriggerTaskResponse), message: body.message }
    }
    return body as unknown as TriggerTaskResponse
  }

  // 取消任务执行
  // 后端已加 alias: POST /api/v1/runs/{id}/cancel
  // 旧路径 POST /api/v1/tasks/runs/{id}/stop 作为兜底
  async cancelTaskRun(runId: string): Promise<{ remote_cancelled: boolean }> {
    try {
      const response = await apiClient.post<ApiResponse<{ remote_cancelled: boolean }>>(
        `/api/v1/runs/${runId}/cancel`
      )
      return response.data.data
    } catch (error) {
      const axiosError = error as AxiosError
      if (axiosError?.response?.status === 404) {
        const fallback = await apiClient.post<ApiResponse<{ remote_cancelled: boolean }>>(
          `/api/v1/tasks/runs/${runId}/stop`
        )
        return fallback.data.data
      }
      throw error
    }
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
