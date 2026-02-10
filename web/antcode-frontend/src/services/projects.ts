import { BaseService } from './base'
import apiClient from './api'
import type { AxiosRequestConfig } from 'axios'
import Logger from '@/utils/logger'
import type {
  Project,
  ProjectCreateRequest,
  ProjectUpdateRequest,
  ProjectListParams,
  ProjectStats,
  ProjectExportConfig,
  ProjectImportConfig,
  ProjectFileContent,
  ProjectFileStructure
} from '@/types'

// Ant Design Upload 组件的文件对象类型
interface UploadFile {
  originFileObj?: File
}
type FileInput = File | UploadFile

class ProjectService extends BaseService {
  constructor() {
    super('/api/v1/projects')
  }

  // 辅助方法：从 File 或 UploadFile 中提取实际的 File 对象
  private extractFile(fileInput: FileInput): File | null {
    if (fileInput instanceof File) {
      return fileInput
    }
    if (fileInput && 'originFileObj' in fileInput && fileInput.originFileObj) {
      return fileInput.originFileObj
    }
    return null
  }

  // 获取项目列表
  async getProjects(
    params?: ProjectListParams,
    config?: AxiosRequestConfig
  ): Promise<{ items: Project[]; page: number; size: number; total: number; pages: number }> {
    const response = await apiClient.get<{
      success: boolean
      data: Project[]
      pagination: {
        page: number
        size: number
        total: number
        pages: number
      }
    }>('/api/v1/projects', {
      ...config,
      params: { ...(params ?? {}), ...(config?.params ?? {}) }
    })

    // 转换后端响应格式为前端期望的格式
    return {
      items: response.data.data || [],
      page: response.data.pagination?.page || 1,
      size: response.data.pagination?.size || 10,
      total: response.data.pagination?.total || 0,
      pages: response.data.pagination?.pages || 1
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
      if (data.file) {
        // 处理主文件上传 - 适配 Ant Design Upload 组件的文件对象
        const fileToUpload = this.extractFile(data.file as FileInput)
        if (fileToUpload) {
          formData.append('file', fileToUpload)
        }
      }
      // 处理附加文件上传
      if (data.additionalFiles && data.additionalFiles.length > 0) {
        data.additionalFiles.forEach((fileItem) => {
          const fileToUpload = this.extractFile(fileItem)
          if (fileToUpload) {
            formData.append('files', fileToUpload)
          }
        })
      }
      if (data.entry_point) {
        formData.append('entry_point', data.entry_point)
      }
      if (data.dependencies) { formData.append('dependencies', JSON.stringify(data.dependencies)) }
    } else if (data.type === 'rule') {
      this.appendRuleFields(formData, data)
    } else if (data.type === 'code') {
      if (data.language) {
        formData.append('language', data.language)
      }
      if (data.version) {
        formData.append('version', data.version)
      }
      if (data.code_content) {
        formData.append('code_content', data.code_content)
      }
      if (data.code_file) {
        formData.append('code_file', data.code_file)
      }
      if (data.code_entry_point) {
        formData.append('code_entry_point', data.code_entry_point)
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
    if (data.anti_spider) {
      formData.append('anti_spider', data.anti_spider)
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
    // v2.0.0 新增字段
    if (data.proxy_config) {
      formData.append('proxy_config', data.proxy_config)
    }
    if (data.task_config) {
      formData.append('task_config', data.task_config)
    }
    if (data.data_schema) {
      formData.append('data_schema', data.data_schema)
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
      'target_url',
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
      'task_config'
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
  async updateCodeConfig(id: string, data: Record<string, unknown>): Promise<Project> {
    return this.put<Project>(`/${id}/code-config`, data)
  }

  // 更新文件项目配置
  async updateFileConfig(id: string, data: Record<string, unknown>): Promise<Project> {
    const formData = new FormData()

    if (data.entry_point) {
      formData.append('entry_point', data.entry_point as string)
    }
    if (data.runtime_config) {
      formData.append('runtime_config', typeof data.runtime_config === 'string' ? data.runtime_config : JSON.stringify(data.runtime_config))
    }
    if (data.environment_vars) {
      formData.append('environment_vars', typeof data.environment_vars === 'string' ? data.environment_vars : JSON.stringify(data.environment_vars))
    }
    if (data.file) {
      formData.append('file', data.file as File)
    }

    return this.uploadFile<Project>(`/${id}/file-config`, formData)
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

  // 导入项目
  async importProject(config: ProjectImportConfig): Promise<Project[]> {
    const formData = new FormData()
    formData.append('file', config.file)
    if (config.name) {
      formData.append('name', config.name)
    }
    if (config.description) {
      formData.append('description', config.description)
    }
    if (config.entry_point) {
      formData.append('entry_point', config.entry_point)
    }
    if (config.runtime_scope) {
      formData.append('runtime_scope', config.runtime_scope)
    }
    if (config.worker_id) {
      formData.append('worker_id', config.worker_id)
    }
    if (config.use_existing_env !== undefined) {
      formData.append('use_existing_env', config.use_existing_env.toString())
    }
    if (config.existing_env_name) {
      formData.append('existing_env_name', config.existing_env_name)
    }
    if (config.python_version) {
      formData.append('python_version', config.python_version)
    }
    if (config.shared_runtime_key) {
      formData.append('shared_runtime_key', config.shared_runtime_key)
    }
    if (config.env_name) {
      formData.append('env_name', config.env_name)
    }
    if (config.env_description) {
      formData.append('env_description', config.env_description)
    }
    if (config.overwrite_existing !== undefined) {
      formData.append('overwrite_existing', config.overwrite_existing.toString())
    }

    return this.uploadFile<Project[]>('/import', formData)
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

  // ============ 文件管理API ============

  // 获取项目文件结构
  async getProjectFileStructure(id: string): Promise<ProjectFileStructure> {
    try {
      return await this.get<ProjectFileStructure>(`/${id}/files/structure`)
    } catch (error) {
      Logger.error('获取项目文件结构失败:', error)
      throw error
    }
  }

  // 获取文件内容
  async getFileContent(id: string, filePath: string): Promise<ProjectFileContent> {
    try {
      if (!filePath) {
        throw new Error('文件路径不能为空')
      }
      
      return await this.get<ProjectFileContent>(`/${id}/files/content`, {
        params: { file_path: filePath }
      })
    } catch (error) {
      Logger.error('获取文件内容失败:', error)
      throw error
    }
  }

  // 更新文件内容
  async updateFileContent(
    id: string,
    payload: { file_path: string; content: string; encoding?: string }
  ): Promise<ProjectFileContent> {
    try {
      if (!payload.file_path) {
        throw new Error('文件路径不能为空')
      }
      if (payload.content === undefined || payload.content === null) {
        throw new Error('文件内容不能为空')
      }
      
      return await this.put<ProjectFileContent>(`/${id}/files/content`, payload)
    } catch (error) {
      Logger.error('更新文件内容失败:', error)
      throw error
    }
  }

  // 下载文件
  async downloadProjectFile(id: string, filePath?: string): Promise<Blob> {
    try {
      const url = filePath 
        ? `/api/v1/projects/${id}/files/download?file_path=${encodeURIComponent(filePath)}`
        : `/api/v1/projects/${id}/files/download`
      
      const response = await apiClient.get(url, {
        responseType: 'blob',
        timeout: 60000  // 下载超时设置为60秒
      })
      
      if (!response.data || response.data.size === 0) {
        throw new Error('下载的文件为空')
      }
      
      return response.data
    } catch (error) {
      Logger.error('下载文件失败:', error)
      throw error
    }
  }

  // 创建下载链接（用于直接下载）
  getDownloadUrl(id: string, filePath?: string): string {
    const baseUrl = apiClient.defaults.baseURL || ''
    const normalizedBase = baseUrl.replace(/\/api\/?$/, '').replace(/\/$/, '')
    return filePath 
      ? `${normalizedBase}/api/v1/projects/${id}/files/download?file_path=${encodeURIComponent(filePath)}`
      : `${normalizedBase}/api/v1/projects/${id}/files/download`
  }
}

export const projectService = new ProjectService()
export default projectService
