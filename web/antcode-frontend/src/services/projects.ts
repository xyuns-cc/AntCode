import { BaseService } from './base'
import apiClient, { unwrapResponse } from './api'
import type { AxiosRequestConfig } from 'axios'
import Logger from '@/utils/logger'
import type {
  PaginationResponse,
  Project,
  ProjectCreateRequest,
  ProjectCodeConfigUpdateRequest,
  ProjectFileConfigUpdateRequest,
  ProjectSourcePayload,
  ProjectUpdateRequest,
  ProjectListParams,
  ProjectStats,
  ProjectExportConfig
} from '@/types'

class ProjectService extends BaseService {
  constructor() {
    super('/api/v1/projects')
  }

  private appendRepositorySourceFields(formData: FormData, data: ProjectCreateRequest): void {
    if (data.repository_id) {
      formData.append('repository_id', data.repository_id)
    }
    if (data.ref) {
      formData.append('ref', data.ref)
    }
    if (data.subdir) {
      formData.append('subdir', data.subdir)
    }
    if (data.include_paths !== undefined) {
      const value = Array.isArray(data.include_paths)
        ? JSON.stringify(data.include_paths)
        : data.include_paths
      formData.append('include_paths', value)
    }
  }

  private appendJsonField(
    formData: FormData,
    field: string,
    value: string | Record<string, unknown> | undefined,
    emptyObjectWhenBlank = false,
  ): void {
    if (value === undefined) {
      return
    }
    if (typeof value === 'string') {
      const normalized = value.trim()
      formData.append(field, !normalized && emptyObjectWhenBlank ? '{}' : value)
      return
    }
    formData.append(field, JSON.stringify(value))
  }

  // 获取项目列表
  async getProjects(
    params?: ProjectListParams,
    config?: AxiosRequestConfig
  ): Promise<{ items: Project[]; page: number; size: number; total: number; pages: number }> {
    const response = await apiClient.get<PaginationResponse<Project>>('/api/v1/projects', {
      ...config,
      params: { ...(params ?? {}), ...(config?.params ?? {}) }
    })

    const { items, pagination } = response.data.data

    return {
      items,
      page: pagination.page,
      size: pagination.size,
      total: pagination.total,
      pages: pagination.pages
    }
  }

  // 获取项目详情
  async getProject(id: string): Promise<Project> {
    return this.get<Project>(`/${id}`)
  }

  // 创建项目
  async createProject(data: ProjectCreateRequest): Promise<Project> {
    const formData = new FormData()

    // 添加基本字段
    formData.append('name', data.name)
    formData.append('type', data.type)
    if (data.description) {
      formData.append('description', data.description)
    }
    if (data.tags) {
      formData.append('tags', Array.isArray(data.tags) ? data.tags.join(',') : data.tags)
    }

    // 环境必填字段
    formData.append('runtime_scope', data.runtime_scope)
    formData.append('python_version', data.python_version)
    if (data.shared_runtime_key) {
      formData.append('shared_runtime_key', data.shared_runtime_key)
    }
    // 环境配置参数
    if (data.env_location) {
      formData.append('env_location', data.env_location)
    }
    if (data.worker_id) {
      formData.append('worker_id', data.worker_id)
    }
    if (data.use_existing_env !== undefined) {
      formData.append('use_existing_env', String(data.use_existing_env))
    }
    if (data.existing_env_name) {
      formData.append('existing_env_name', data.existing_env_name)
    }
    if (data.env_name) {
      formData.append('env_name', data.env_name)
    }
    if (data.env_description) {
      formData.append('env_description', data.env_description)
    }

    // 根据项目类型添加特定字段
    if (data.type === 'file') {
      this.appendRepositorySourceFields(formData, data)
      if (data.entry_point) {
        formData.append('entry_point', data.entry_point)
      }
      this.appendJsonField(formData, 'runtime_config', data.runtime_config, true)
      this.appendJsonField(formData, 'environment_vars', data.environment_vars, true)
      if (data.dependencies) { formData.append('dependencies', JSON.stringify(data.dependencies)) }
    } else if (data.type === 'rule') {
      this.appendRuleFields(formData, data)
    } else if (data.type === 'code') {
      this.appendRepositorySourceFields(formData, data)
      if (data.language) {
        formData.append('language', data.language)
      }
      if (data.code_entry_point) {
        formData.append('code_entry_point', data.code_entry_point)
        formData.append('entry_point', data.code_entry_point)
      }
      if (data.documentation) {
        formData.append('documentation', data.documentation)
      }
      if (data.dependencies) { formData.append('dependencies', JSON.stringify(data.dependencies)) }
    }

    return this.uploadFile<Project>('', formData)
  }

  // 辅助方法：添加规则项目字段到 FormData
  private appendRuleFields(formData: FormData, data: Partial<ProjectCreateRequest | ProjectUpdateRequest>): void {
    if (data.target_url) {
      formData.append('target_url', data.target_url)
    }
    if (data.url_pattern) {
      formData.append('url_pattern', data.url_pattern)
    }
    if (data.engine) {
      formData.append('engine', data.engine)
    }
    if (data.request_delay !== undefined) {
      formData.append('request_delay', data.request_delay.toString())
    }
    if (data.extraction_rules) {
      formData.append('extraction_rules', data.extraction_rules)
    }
    if (data.pagination_config) {
      formData.append('pagination_config', data.pagination_config)
    }
    if (data.request_method) {
      formData.append('request_method', data.request_method)
    }
    if (data.headers) {
      formData.append('headers', JSON.stringify(data.headers))
    }
    if (data.cookies) {
      formData.append('cookies', JSON.stringify(data.cookies))
    }
    if (data.callback_type) {
      formData.append('callback_type', data.callback_type)
    }
    if (data.priority !== undefined) {
      formData.append('priority', data.priority.toString())
    }
    this.appendJsonField(formData, 'proxy_config', data.proxy_config)
    this.appendJsonField(formData, 'anti_spider', data.anti_spider)
    this.appendJsonField(formData, 'task_config', data.task_config)
    this.appendJsonField(formData, 'data_schema', data.data_schema)
    // S10 (Scrapy 迁移收尾): dedup_config 走 JSON 字段，resume_enabled 走 bool 字符串
    this.appendJsonField(formData, 'dedup_config', (data as Record<string, unknown>).dedup_config)
    const resumeVal = (data as Record<string, unknown>).resume_enabled
    if (resumeVal !== undefined && resumeVal !== null) {
      formData.append('resume_enabled', String(Boolean(resumeVal)))
    }
    if (data.retry_count !== undefined) {
      formData.append('retry_count', data.retry_count.toString())
    }
    if (data.timeout !== undefined) {
      formData.append('timeout', data.timeout.toString())
    }
    if (data.dont_filter !== undefined) {
      formData.append('dont_filter', data.dont_filter.toString())
    }
    if (data.dependencies) { formData.append('dependencies', JSON.stringify(data.dependencies)) }
  }

  // 更新项目
  async updateProject(id: string, data: ProjectUpdateRequest): Promise<Project> {
    return this.put<Project>(`/${id}`, data)
  }

  // 更新规则项目配置
  async updateRuleConfig(id: string, data: Partial<ProjectUpdateRequest>): Promise<Project> {
    const payload: Record<string, unknown> = {}
    const allowedFields = [
      'engine',
      'target_url',
      'url_pattern',
      'callback_type',
      'request_method',
      'extraction_rules',
      'pagination_config',
      'max_pages',
      'start_page',
      'request_delay',
      'priority',
      'dont_filter',
      'headers',
      'cookies',
      'proxy_config',
      'anti_spider',
      'task_config',
      'data_schema',
      'retry_count',
      'timeout',
      // S10 (Scrapy 迁移收尾): 前端提交但后端没接过的两个字段
      'resume_enabled',
      'dedup_config'
    ]
    allowedFields.forEach((field) => {
      const value = data[field as keyof ProjectUpdateRequest]
      if (value !== undefined) {
        payload[field] = value
      }
    })

    const parseJson = (value: unknown, field: string) => {
      if (value === undefined || value === null) return value
      if (typeof value !== 'string') return value
      if (!value.trim()) return undefined
      try {
        return JSON.parse(value)
      } catch {
        throw new Error(`${field} JSON解析失败`)
      }
    }

    if (payload.extraction_rules !== undefined) {
      const parsed = parseJson(payload.extraction_rules, 'extraction_rules')
      if (!Array.isArray(parsed)) {
        throw new Error('extraction_rules 必须是数组')
      }
      payload.extraction_rules = parsed
    }

    if (payload.pagination_config !== undefined) {
      const parsed = parseJson(payload.pagination_config, 'pagination_config')
      if (parsed && typeof parsed !== 'object') {
        throw new Error('pagination_config 必须是对象')
      }
      payload.pagination_config = parsed
    }

    if (payload.headers !== undefined) {
      payload.headers = parseJson(payload.headers, 'headers')
    }

    if (payload.cookies !== undefined) {
      payload.cookies = parseJson(payload.cookies, 'cookies')
    }

    if (payload.task_config !== undefined) {
      payload.task_config = parseJson(payload.task_config, 'task_config')
    }

    if (payload.proxy_config !== undefined) {
      payload.proxy_config = parseJson(payload.proxy_config, 'proxy_config')
    }

    if (payload.anti_spider !== undefined) {
      payload.anti_spider = parseJson(payload.anti_spider, 'anti_spider')
    }

    if (payload.data_schema !== undefined) {
      payload.data_schema = parseJson(payload.data_schema, 'data_schema')
    }

    const numberFields = ['max_pages', 'start_page', 'request_delay', 'priority', 'retry_count', 'timeout']
    numberFields.forEach((field) => {
      const value = payload[field]
      if (typeof value === 'string') {
        const parsed = Number(value)
        if (Number.isNaN(parsed)) {
          throw new Error(`${field} 必须是数字`)
        }
        payload[field] = parsed
      }
    })

    if (typeof payload.dont_filter === 'string') {
      payload.dont_filter = payload.dont_filter === 'true'
    }

    return this.put<Project>(`/${id}/rule-config`, payload)
  }

  // 更新代码项目配置
  async updateCodeConfig(id: string, data: ProjectCodeConfigUpdateRequest): Promise<Project> {
    return this.put<Project>(`/${id}/code-config`, data)
  }

  async getProjectSource(id: string): Promise<ProjectSourcePayload> {
    return this.get<ProjectSourcePayload>(`/${id}/source`)
  }

  async updateProjectSource(id: string, data: ProjectSourcePayload): Promise<ProjectSourcePayload> {
    return this.put<ProjectSourcePayload>(`/${id}/source`, data)
  }

  // 更新文件项目配置
  async updateFileConfig(id: string, data: ProjectFileConfigUpdateRequest): Promise<Project> {
    const formData = new FormData()

    if (data.entry_point) {
      formData.append('entry_point', data.entry_point as string)
    }
    this.appendJsonField(formData, 'runtime_config', data.runtime_config, true)
    this.appendJsonField(formData, 'environment_vars', data.environment_vars, true)

    const response = await apiClient.put(`${this.basePath}/${id}/file-config`, formData, {
      headers: {
        'Content-Type': 'multipart/form-data',
      },
    })
    return unwrapResponse<Project>(response)
  }

  // 删除项目
  async deleteProject(id: string): Promise<void> {
    try {
      await this.delete(`/${id}`)
    } catch (error) {
      Logger.error('删除项目失败:', error)
      throw error
    }
  }

  // 批量删除项目
  async batchDeleteProjects(ids: string[]): Promise<{
    total: number
    success_count: number
    failed_count: number
    failed_projects: string[]
  }> {
    try {
      return await this.post<{
        total: number
        success_count: number
        failed_count: number
        failed_projects: string[]
      }>('/batch-delete', { project_ids: ids })
    } catch (error) {
      Logger.error('批量删除项目失败:', error)
      throw error
    }
  }

  // 复制项目
  async duplicateProject(id: string, name?: string): Promise<Project> {
    return this.post<Project>(`/${id}/duplicate`, { name })
  }

  // 获取项目统计信息
  async getProjectStats(): Promise<ProjectStats> {
    return this.get<ProjectStats>('/stats')
  }

  // 导出项目
  async exportProject(id: string, config: ProjectExportConfig): Promise<Blob> {
    const response = await apiClient.post(
      `/api/v1/projects/${id}/export`,
      config,
      {
        responseType: 'blob',
      }
    )
    return response.data
  }

  // 导入项目（上传文件/压缩包 + 环境配置，multipart/form-data）
  async importProject(params: {
    file: File
    name?: string
    description?: string
    entry_point?: string
    runtime_scope: string
    worker_id?: string
    use_existing_env?: boolean
    existing_env_name?: string
    python_version?: string
    shared_runtime_key?: string
    env_name?: string
    env_description?: string
    overwrite_existing?: boolean
    runtime_config?: string
    environment_vars?: string
  }): Promise<unknown> {
    const form = new FormData()
    form.append('file', params.file)
    form.append('runtime_scope', params.runtime_scope)
    if (params.worker_id) form.append('worker_id', params.worker_id)
    const optional: Array<[string, unknown]> = [
      ['name', params.name],
      ['description', params.description],
      ['entry_point', params.entry_point],
      ['use_existing_env', params.use_existing_env],
      ['existing_env_name', params.existing_env_name],
      ['python_version', params.python_version],
      ['shared_runtime_key', params.shared_runtime_key],
      ['env_name', params.env_name],
      ['env_description', params.env_description],
      ['overwrite_existing', params.overwrite_existing],
      ['runtime_config', params.runtime_config],
      ['environment_vars', params.environment_vars],
    ]
    for (const [k, v] of optional) {
      if (v !== undefined && v !== null && v !== '') form.append(k, String(v))
    }
    const response = await apiClient.post('/api/v1/projects/import', form, {
      headers: { 'Content-Type': 'multipart/form-data' },
    })
    return response.data
  }

  // 验证项目配置
  async validateProject(data: ProjectCreateRequest): Promise<{ valid: boolean; errors?: string[] }> {
    return this.post<{ valid: boolean; errors?: string[] }>('/validate', data)
  }

  // 测试项目连接（规则项目）
  async testProjectConnection(id: string): Promise<{ success: boolean; message: string; data?: Record<string, unknown> }> {
    return this.post<{ success: boolean; message: string; data?: Record<string, unknown> }>(`/${id}/test-connection`)
  }

  // 获取项目依赖
  async getProjectDependencies(id: string): Promise<string[]> {
    const result = await this.get<{ dependencies: string[] }>(`/${id}/dependencies`)
    return result.dependencies
  }

  // 更新项目依赖
  async updateProjectDependencies(id: string, dependencies: string[]): Promise<void> {
    await this.put(`/${id}/dependencies`, { dependencies })
  }

  // 获取项目标签列表
  async getProjectTags(): Promise<string[]> {
    const result = await this.get<{ tags: string[] }>('/tags')
    return result.tags
  }

  // 搜索项目
  async searchProjects(query: string, filters?: Partial<ProjectListParams>): Promise<Project[]> {
    const params = {
      search: query,
      ...filters,
    }
    const result = await this.get<{ projects: Project[] }>('/search', { params })
    return result.projects
  }

}

export const projectService = new ProjectService()
export default projectService
