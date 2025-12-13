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
  TaskExecuteRequest,
  TaskStats,
  TaskBatchRequest,
  PaginatedResponse,
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
  async batchDeleteTasks(ids: (string | number)[]): Promise<{ success_count: number; failed_count: number; failed_ids: (string | number)[] }> {
    return this.post<{ success_count: number; failed_count: number; failed_ids: (string | number)[] }>(
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

  // 执行任务
  async executeTask(data: TaskExecuteRequest): Promise<TaskExecution> {
    // 使用 /api/v1/tasks 路径
    const response = await apiClient.post<ApiResponse<TaskExecution>>(
      `/api/v1/tasks/${data.task_id}/execute`,
      {
        execution_config: data.execution_config,
        environment_variables: data.environment_variables,
      }
    )
    return response.data.data
  }

  // 停止任务执行
  async stopTaskExecution(executionId: string): Promise<void> {
    await apiClient.post(`/api/v1/tasks/executions/${executionId}/stop`)
  }

  // 取消任务执行
  async cancelTaskExecution(executionId: string): Promise<{ remote_cancelled: boolean }> {
    const response = await apiClient.post<{ remote_cancelled: boolean }>(
      `/api/v1/scheduler/executions/${executionId}/cancel`
    )
    return response.data
  }

  // 获取任务执行记录
  async getTaskExecutions(taskId: string, params?: {
    page?: number
    size?: number
    status?: string
  }): Promise<{ items: TaskExecution[]; total: number }> {
    const response = await apiClient.get<{ items: TaskExecution[]; total: number }>(
      `/api/v1/scheduler/tasks/${taskId}/executions`,
      { params }
    )
    return response.data
  }

  // 获取任务执行详情
  async getTaskExecution(executionId: string): Promise<TaskExecution> {
    return this.get<TaskExecution>(`/executions/${executionId}`)
  }

  // 获取任务执行日志
  async getTaskExecutionLogs(executionId: string, params?: {
    page?: number
    size?: number
    level?: string
  }): Promise<PaginatedResponse<Record<string, unknown>>> {
    const response = await apiClient.get<PaginatedResponse<Record<string, unknown>>>(
      `/api/v1/tasks/executions/${executionId}/logs`,
      { params }
    )
    return response.data
  }

  // 下载任务执行日志
  async downloadTaskExecutionLogs(executionId: string, format: 'txt' | 'json' = 'txt'): Promise<Blob> {
    const response = await apiClient.get(
      `/api/v1/tasks/executions/${executionId}/logs/download`,
      {
        params: { format },
        responseType: 'blob',
      }
    )
    return response.data
  }

  // 获取任务统计信息
  async getTaskStats(projectId?: string): Promise<TaskStats> {
    const params = projectId ? { project_id: projectId } : undefined
    const response = await apiClient.get<ApiResponse<TaskStats>>('/api/v1/tasks/stats', { params })
    return response.data.data
  }

  // 批量操作任务
  async batchOperateTasks(data: TaskBatchRequest): Promise<void> {
    await apiClient.post('/api/v1/tasks/batch', data)
  }

  // 启用/禁用任务
  async toggleTaskStatus(id: string, enabled: boolean): Promise<Task> {
    return this.patch<Task>(`/tasks/${id}/toggle`, { enabled })
  }

  // 复制任务
  async duplicateTask(id: string, name?: string): Promise<Task> {
    return this.post<Task>(`/tasks/${id}/duplicate`, { name })
  }

  // 获取任务调度历史
  async getTaskScheduleHistory(id: string, params?: {
    page?: number
    size?: number
    start_date?: string
    end_date?: string
  }): Promise<PaginatedResponse<Record<string, unknown>>> {
    const response = await apiClient.get<PaginatedResponse<Record<string, unknown>>>(
      `/api/v1/tasks/${id}/schedule-history`,
      { params }
    )
    return response.data
  }

  // 验证Cron表达式
  async validateCronExpression(expression: string): Promise<{
    valid: boolean
    next_runs?: string[]
    error?: string
  }> {
    const response = await apiClient.post<ApiResponse<{
      valid: boolean
      next_runs?: string[]
      error?: string
    }>>('/api/v1/tasks/validate-cron', { expression })
    return response.data.data
  }

  // 获取任务模板
  async getTaskTemplates(): Promise<Array<Record<string, unknown>>> {
    const response = await apiClient.get<ApiResponse<{ templates: Array<Record<string, unknown>> }>>('/api/v1/tasks/templates')
    return response.data.data.templates
  }

  // 从模板创建任务
  async createTaskFromTemplate(templateId: string, data: Partial<TaskCreateRequest>): Promise<Task> {
    const response = await apiClient.post<ApiResponse<Task>>(
      `/api/v1/tasks/templates/${templateId}/create`,
      data
    )
    return response.data.data
  }

  // 导出任务配置
  async exportTask(id: string, format: 'json' | 'yaml' = 'json'): Promise<Blob> {
    const response = await apiClient.get(
      `/api/v1/tasks/${id}/export`,
      {
        params: { format },
        responseType: 'blob',
      }
    )
    return response.data
  }

  // 导入任务配置
  async importTask(file: File, projectId: string): Promise<Task> {
    const formData = new FormData()
    formData.append('file', file)
    formData.append('project_id', projectId)

    const response = await apiClient.post<ApiResponse<Task>>(
      '/api/v1/tasks/import',
      formData,
      {
        headers: {
          'Content-Type': 'multipart/form-data',
        },
      }
    )
    return response.data.data
  }

  // 获取任务依赖关系
  async getTaskDependencies(id: string): Promise<{
    dependencies: Task[]
    dependents: Task[]
  }> {
    const response = await apiClient.get<ApiResponse<{
      dependencies: Task[]
      dependents: Task[]
    }>>(`/api/v1/tasks/${id}/dependencies`)
    return response.data.data
  }

  // 设置任务依赖关系
  async setTaskDependencies(id: string, dependencyIds: string[]): Promise<void> {
    await apiClient.put(`/api/v1/tasks/${id}/dependencies`, {
      dependency_ids: dependencyIds
    })
  }
}

export const taskService = new TaskService()
export default taskService
