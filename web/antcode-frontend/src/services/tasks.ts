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

class TaskService {
  // 获取任务列表
  async getTasks(params?: TaskListParams): Promise<TaskListResponse> {
    const response = await apiClient.get<TaskListResponse>('/api/v1/scheduler/tasks', { params })
    return response.data
  }

  // 获取任务详情
  async getTask(id: number): Promise<Task> {
    const response = await apiClient.get<ApiResponse<Task>>(`/api/v1/scheduler/tasks/${id}`)
    return response.data.data
  }

  // 创建任务
  async createTask(data: TaskCreateRequest): Promise<Task> {
    const response = await apiClient.post<ApiResponse<Task>>('/api/v1/scheduler/tasks', data)
    return response.data.data
  }

  // 更新任务
  async updateTask(id: number, data: TaskUpdateRequest): Promise<Task> {
    const response = await apiClient.put<ApiResponse<Task>>(`/api/v1/scheduler/tasks/${id}`, data)
    return response.data.data
  }

  // 删除任务
  async deleteTask(id: number): Promise<void> {
    await apiClient.delete(`/api/v1/scheduler/tasks/${id}`)
  }

  // 立即触发任务
  async triggerTask(id: number): Promise<ApiResponse<any>> {
    console.log('[DEBUG tasksService] triggerTask -> start', { id })
    const response = await apiClient.post<ApiResponse<any>>(
      `/api/v1/scheduler/tasks/${id}/trigger`,
      undefined,
      { headers: { 'X-Skip-Success-Toast': '1' } }
    )
    console.log('[DEBUG tasksService] triggerTask -> response', response?.status, response?.data)
    return response.data
  }



  // 执行任务
  async executeTask(data: TaskExecuteRequest): Promise<TaskExecution> {
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
  async cancelTaskExecution(executionId: string): Promise<void> {
    await apiClient.post(`/api/v1/tasks/executions/${executionId}/cancel`)
  }

  // 获取任务执行记录
  async getTaskExecutions(taskId: number, params?: {
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
    const response = await apiClient.get<ApiResponse<TaskExecution>>(
      `/api/v1/scheduler/executions/${executionId}`
    )
    return response.data.data
  }

  // 获取任务执行日志
  async getTaskExecutionLogs(executionId: string, params?: {
    page?: number
    size?: number
    level?: string
  }): Promise<PaginatedResponse<any>> {
    const response = await apiClient.get<PaginatedResponse<any>>(
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
  async getTaskStats(projectId?: number): Promise<TaskStats> {
    const params = projectId ? { project_id: projectId } : undefined
    const response = await apiClient.get<ApiResponse<TaskStats>>('/api/v1/tasks/stats', { params })
    return response.data.data
  }

  // 批量操作任务
  async batchOperateTasks(data: TaskBatchRequest): Promise<void> {
    await apiClient.post('/api/v1/tasks/batch', data)
  }

  // 启用/禁用任务
  async toggleTaskStatus(id: number, enabled: boolean): Promise<Task> {
    const response = await apiClient.patch<ApiResponse<Task>>(
      `/api/v1/tasks/${id}/toggle`,
      { enabled }
    )
    return response.data.data
  }

  // 复制任务
  async duplicateTask(id: number, name?: string): Promise<Task> {
    const response = await apiClient.post<ApiResponse<Task>>(
      `/api/v1/tasks/${id}/duplicate`,
      { name }
    )
    return response.data.data
  }

  // 获取任务调度历史
  async getTaskScheduleHistory(id: number, params?: {
    page?: number
    size?: number
    start_date?: string
    end_date?: string
  }): Promise<PaginatedResponse<any>> {
    const response = await apiClient.get<PaginatedResponse<any>>(
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
  async getTaskTemplates(): Promise<any[]> {
    const response = await apiClient.get<ApiResponse<{ templates: any[] }>>('/api/v1/tasks/templates')
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
  async exportTask(id: number, format: 'json' | 'yaml' = 'json'): Promise<Blob> {
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
  async importTask(file: File, projectId: number): Promise<Task> {
    const formData = new FormData()
    formData.append('file', file)
    formData.append('project_id', projectId.toString())

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
  async getTaskDependencies(id: number): Promise<{
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
  async setTaskDependencies(id: number, dependencyIds: number[]): Promise<void> {
    await apiClient.put(`/api/v1/tasks/${id}/dependencies`, {
      dependency_ids: dependencyIds
    })
  }
}

export const taskService = new TaskService()
export default taskService
