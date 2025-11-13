import apiClient from './api'
import Logger from '@/utils/logger'
import type {
  Project,
  ProjectCreateRequest,
  ProjectUpdateRequest,
  ProjectListParams,
  ProjectStats,
  ProjectExportConfig,
  ProjectImportConfig,
  ApiResponse,
  PaginatedResponse,
  ProjectFileContent
} from '@/types'

class ProjectService {
  // 获取项目列表
  async getProjects(params?: ProjectListParams): Promise<{ items: Project[]; page: number; size: number; total: number; pages: number }> {
    const response = await apiClient.get<{
      success: boolean
      data: Project[]
      pagination: {
        page: number
        size: number
        total: number
        pages: number
      }
    }>('/api/v1/projects', { params })



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
  async getProject(id: number): Promise<Project> {
    const response = await apiClient.get<ApiResponse<Project>>(`/api/v1/projects/${id}`)
    return response.data.data
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
      formData.append('tags', data.tags)
    }

    // 环境必填字段
    if ((data as any).venv_scope) {
      formData.append('venv_scope', (data as any).venv_scope)
    }
    if ((data as any).python_version) {
      formData.append('python_version', (data as any).python_version)
    }
    if ((data as any).shared_venv_key) {
      formData.append('shared_venv_key', (data as any).shared_venv_key!)
    }
    if ((data as any).interpreter_source) {
      formData.append('interpreter_source', (data as any).interpreter_source)
    }
    if ((data as any).python_bin) {
      formData.append('python_bin', (data as any).python_bin)
    }

    // 根据项目类型添加特定字段
    if (data.type === 'file') {
      if (data.file) {
        // 处理主文件上传 - 兼容 Ant Design Upload 组件的文件对象
        const fileToUpload = data.file.originFileObj || data.file
        formData.append('file', fileToUpload)
      }
      // 处理附加文件上传
      if (data.additionalFiles && data.additionalFiles.length > 0) {
        data.additionalFiles.forEach((file: any) => {
          const fileToUpload = file.originFileObj || file
          formData.append('files', fileToUpload)
        })
      }
      if (data.entry_point) {
        formData.append('entry_point', data.entry_point)
      }
      if (data.dependencies) { formData.append('dependencies', JSON.stringify(data.dependencies)) }
    } else if (data.type === 'rule') {
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
        // extraction_rules 期望直接是JSON字符串格式，不需要额外的JSON.stringify
        formData.append('extraction_rules', data.extraction_rules)
      }
      if (data.pagination_config) {
        // pagination_config 期望直接是JSON字符串格式，不需要额外的JSON.stringify
        formData.append('pagination_config', data.pagination_config)
      }
      if (data.anti_spider) {
        // anti_spider 是JSON字符串格式
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

    const response = await apiClient.post<ApiResponse<Project>>(
      '/api/v1/projects',
      formData,
      {
        headers: {
          'Content-Type': 'multipart/form-data',
        },
      }
    )

    return response.data.data
  }

  // 更新项目
  async updateProject(id: number, data: ProjectUpdateRequest): Promise<Project> {
    const response = await apiClient.put<ApiResponse<Project>>(
      `/api/v1/projects/${id}`,
      data
    )
    return response.data.data
  }

  // 更新规则项目配置
  async updateRuleConfig(id: number, data: any): Promise<Project> {
    const formData = new FormData()
    
    // 将所有字段添加到FormData中
    if (data.target_url) {
      formData.append('target_url', data.target_url)
    }
    if (data.url_pattern) {
      formData.append('url_pattern', data.url_pattern)
    }
    if (data.engine) {
      formData.append('engine', data.engine)
    }
    if (data.request_method) {
      formData.append('request_method', data.request_method)
    }
    if (data.callback_type) {
      formData.append('callback_type', data.callback_type)
    }
    if (data.extraction_rules) {
      // extraction_rules 应该是JSON字符串格式
      formData.append('extraction_rules', data.extraction_rules)
    }
    if (data.pagination_config) {
      // pagination_config 应该是JSON字符串格式
      formData.append('pagination_config', data.pagination_config)
    }
    if (data.max_pages !== undefined) {
      formData.append('max_pages', data.max_pages.toString())
    }
    if (data.start_page !== undefined) {
      formData.append('start_page', data.start_page.toString())
    }
    if (data.request_delay !== undefined) {
      formData.append('request_delay', data.request_delay.toString())
    }
    if (data.retry_count !== undefined) {
      formData.append('retry_count', data.retry_count.toString())
    }
    if (data.timeout !== undefined) {
      formData.append('timeout', data.timeout.toString())
    }
    if (data.priority !== undefined) {
      formData.append('priority', data.priority.toString())
    }
    if (data.dont_filter !== undefined) {
      formData.append('dont_filter', data.dont_filter.toString())
    }
    if (data.headers) {
      // headers 需要JSON字符串格式
      formData.append('headers', typeof data.headers === 'string' ? data.headers : JSON.stringify(data.headers))
    }
    if (data.cookies) {
      // cookies 需要JSON字符串格式
      formData.append('cookies', typeof data.cookies === 'string' ? data.cookies : JSON.stringify(data.cookies))
    }
    if (data.proxy_config) {
      formData.append('proxy_config', data.proxy_config)
    }
    if (data.anti_spider) {
      formData.append('anti_spider', data.anti_spider)
    }
    if (data.task_config) {
      formData.append('task_config', data.task_config)
    }

    const response = await apiClient.put<ApiResponse<Project>>(
      `/api/v1/projects/${id}/rule-config`,
      formData,
      {
        headers: {
          'Content-Type': 'multipart/form-data',
        },
      }
    )
    return response.data.data
  }

  // 更新代码项目配置
  async updateCodeConfig(id: number, data: any): Promise<Project> {
    const response = await apiClient.put<ApiResponse<Project>>(
      `/api/v1/projects/${id}/code-config`,
      data
    )
    return response.data.data
  }

  // 更新文件项目配置
  async updateFileConfig(id: number, data: any): Promise<Project> {
    // 如果包含文件，使用FormData
    if (data.file) {
      const formData = new FormData()

      if (data.entry_point) {
        formData.append('entry_point', data.entry_point)
      }
      if (data.runtime_config) {
        formData.append('runtime_config', typeof data.runtime_config === 'string' ? data.runtime_config : JSON.stringify(data.runtime_config))
      }
      if (data.environment_vars) {
        formData.append('environment_vars', typeof data.environment_vars === 'string' ? data.environment_vars : JSON.stringify(data.environment_vars))
      }
      if (data.file) {
        formData.append('file', data.file)
      }

      const response = await apiClient.put<ApiResponse<Project>>(
        `/api/v1/projects/${id}/file-config`,
        formData,
        {
          headers: {
            'Content-Type': 'multipart/form-data',
          },
        }
      )
      return response.data.data
    } else {
      // 没有文件时使用普通的表单数据
      const formData = new FormData()

      if (data.entry_point) {
        formData.append('entry_point', data.entry_point)
      }
      if (data.runtime_config) {
        formData.append('runtime_config', typeof data.runtime_config === 'string' ? data.runtime_config : JSON.stringify(data.runtime_config))
      }
      if (data.environment_vars) {
        formData.append('environment_vars', typeof data.environment_vars === 'string' ? data.environment_vars : JSON.stringify(data.environment_vars))
      }

      const response = await apiClient.put<ApiResponse<Project>>(
        `/api/v1/projects/${id}/file-config`,
        formData,
        {
          headers: {
            'Content-Type': 'multipart/form-data',
          },
        }
      )
      return response.data.data
    }
  }

  // 删除项目
  async deleteProject(id: number): Promise<void> {
    try {
      const response = await apiClient.delete<ApiResponse<null>>(`/api/v1/projects/${id}`)

      if (!response.data.success) {
        throw new Error(response.data.message || '删除项目失败')
      }
    } catch (error) {
      Logger.error('删除项目失败:', error)
      throw error
    }
  }

  // 批量删除项目
  async batchDeleteProjects(ids: number[]): Promise<{
    total: number
    success_count: number
    failed_count: number
    failed_projects: number[]
  }> {
    try {
      const response = await apiClient.post<ApiResponse<{
        total: number
        success_count: number
        failed_count: number
        failed_projects: number[]
      }>>('/api/v1/projects/batch-delete', {
        project_ids: ids
      })

      if (!response.data.success) {
        throw new Error(response.data.message || '批量删除项目失败')
      }

      return response.data.data
    } catch (error) {
      Logger.error('批量删除项目失败:', error)
      throw error
    }
  }



  // 复制项目
  async duplicateProject(id: number, name?: string): Promise<Project> {
    const response = await apiClient.post<ApiResponse<Project>>(
      `/api/v1/projects/${id}/duplicate`,
      { name }
    )
    return response.data.data
  }

  // 获取项目统计信息
  async getProjectStats(): Promise<ProjectStats> {
    const response = await apiClient.get<ApiResponse<ProjectStats>>('/api/v1/projects/stats')
    return response.data.data
  }

  // 导出项目
  async exportProject(id: number, config: ProjectExportConfig): Promise<Blob> {
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
    if (config.overwrite_existing !== undefined) {
      formData.append('overwrite_existing', config.overwrite_existing.toString())
    }
    if (config.import_tasks !== undefined) {
      formData.append('import_tasks', config.import_tasks.toString())
    }
    if (config.import_logs !== undefined) {
      formData.append('import_logs', config.import_logs.toString())
    }

    const response = await apiClient.post<ApiResponse<Project[]>>(
      '/api/v1/projects/import',
      formData,
      {
        headers: {
          'Content-Type': 'multipart/form-data',
        },
      }
    )
    return response.data.data
  }

  // 验证项目配置
  async validateProject(data: ProjectCreateRequest): Promise<{ valid: boolean; errors?: string[] }> {
    const response = await apiClient.post<ApiResponse<{ valid: boolean; errors?: string[] }>>(
      '/api/v1/projects/validate',
      data
    )
    return response.data.data
  }

  // 测试项目连接（规则项目）
  async testProjectConnection(id: number): Promise<{ success: boolean; message: string; data?: any }> {
    const response = await apiClient.post<ApiResponse<{ success: boolean; message: string; data?: any }>>(
      `/api/v1/projects/${id}/test-connection`
    )
    return response.data.data
  }

  // 获取项目文件内容
  async getProjectFileContent(id: number): Promise<string> {
    const response = await apiClient.get<ApiResponse<{ content: string }>>(
      `/api/v1/projects/${id}/file-content`
    )
    return response.data.data.content
  }

  // 更新项目文件内容
  async updateProjectFileContent(id: number, content: string): Promise<void> {
    await apiClient.put(`/api/v1/projects/${id}/file-content`, { content })
  }

  // 获取项目依赖
  async getProjectDependencies(id: number): Promise<string[]> {
    const response = await apiClient.get<ApiResponse<{ dependencies: string[] }>>(
      `/api/v1/projects/${id}/dependencies`
    )
    return response.data.data.dependencies
  }

  // 更新项目依赖
  async updateProjectDependencies(id: number, dependencies: string[]): Promise<void> {
    await apiClient.put(`/api/v1/projects/${id}/dependencies`, { dependencies })
  }

  // 获取项目标签列表
  async getProjectTags(): Promise<string[]> {
    const response = await apiClient.get<ApiResponse<{ tags: string[] }>>('/api/v1/projects/tags')
    return response.data.data.tags
  }

  // 搜索项目
  async searchProjects(query: string, filters?: Partial<ProjectListParams>): Promise<Project[]> {
    const params = {
      search: query,
      ...filters,
    }
    const response = await apiClient.get<ApiResponse<{ projects: Project[] }>>(
      '/api/v1/projects/search',
      { params }
    )
    return response.data.data.projects
  }

  // ============ 新增文件管理API ============

  // 获取项目文件结构
  // 获取项目文件结构
  async getProjectFileStructure(id: number): Promise<any> {
    try {
      const response = await apiClient.get<ApiResponse<any>>(
        `/api/v1/projects/${id}/files/structure`
      )
      return response.data.data
    } catch (error) {
      Logger.error('获取项目文件结构失败:', error)
      throw error
    }
  }

  // 获取文件内容
  async getFileContent(id: number, filePath: string): Promise<ProjectFileContent> {
    try {
      if (!filePath) {
        throw new Error('文件路径不能为空')
      }
      
      const response = await apiClient.get<ApiResponse<ProjectFileContent>>(
        `/api/v1/projects/${id}/files/content`,
        {
          params: { file_path: filePath }
        }
      )
      return response.data.data
    } catch (error) {
      Logger.error('获取文件内容失败:', error)
      throw error
    }
  }

  // 更新文件内容
  async updateFileContent(
    id: number,
    payload: { file_path: string; content: string; encoding?: string }
  ): Promise<ProjectFileContent> {
    try {
      if (!payload.file_path) {
        throw new Error('文件路径不能为空')
      }
      if (payload.content === undefined || payload.content === null) {
        throw new Error('文件内容不能为空')
      }
      
      const response = await apiClient.put<ApiResponse<ProjectFileContent>>(
        `/api/v1/projects/${id}/files/content`,
        payload
      )
      return response.data.data
    } catch (error) {
      Logger.error('更新文件内容失败:', error)
      throw error
    }
  }

  // 下载文件
  async downloadFile(id: number, filePath?: string): Promise<Blob> {
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
  getDownloadUrl(id: number, filePath?: string): string {
    const baseUrl = apiClient.defaults.baseURL || ''
    return filePath 
      ? `${baseUrl}/api/v1/projects/${id}/files/download?file_path=${encodeURIComponent(filePath)}`
      : `${baseUrl}/api/v1/projects/${id}/files/download`
  }
}

export const projectService = new ProjectService()
export default projectService
