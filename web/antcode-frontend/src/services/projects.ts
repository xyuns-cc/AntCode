import { BaseService } from './base'
import apiClient, { unwrapResponse } from './api'
import {
  buildRuleConfigPayload,
  createFileConfigFormData,
  createProjectFormData,
} from './projectPayloads'
import type { AxiosRequestConfig } from 'axios'
import Logger from '@/utils/logger'
import type {
  PaginationResponse,
  Project,
  ProjectCreateRequest,
  ProjectCodeConfigUpdateRequest,
  ProjectFileConfigUpdateRequest,
  ProjectSourceInfo,
  ProjectSourcePayload,
  ProjectUpdateRequest,
  ProjectListParams,
  ProjectStats,
  ProjectExportConfig,
} from '@/types'

const PROJECT_OPTION_PAGE_SIZE = 500

interface ProjectPage {
  items: Project[]
  page: number
  size: number
  total: number
  pages: number
}

function assertProjectPage(result: ProjectPage, requestedPage: number): void {
  if (result.page !== requestedPage) {
    throw new Error(`项目分页页码不一致: requested=${requestedPage}, received=${result.page}`)
  }
  if (result.size <= 0) {
    throw new Error(`项目分页 size 非法: ${result.size}`)
  }
  if (result.total < 0 || result.pages < 0) {
    throw new Error(`项目分页元数据非法: total=${result.total}, pages=${result.pages}`)
  }
  const expectedPages = Math.ceil(result.total / result.size)
  if (result.pages !== expectedPages) {
    throw new Error(`项目分页页数不一致: expected=${expectedPages}, received=${result.pages}`)
  }
}

function appendDistinctProjects(target: Project[], seenIds: Set<string>, items: Project[]): void {
  items.forEach((project) => {
    if (seenIds.has(project.id)) {
      throw new Error(`项目分页包含重复项目: ${project.id}`)
    }
    seenIds.add(project.id)
    target.push(project)
  })
}

class ProjectService extends BaseService {
  constructor() {
    super('/api/v1/projects')
  }

  // 获取项目列表
  async getProjects(
    params?: ProjectListParams,
    config?: AxiosRequestConfig
  ): Promise<{ items: Project[]; page: number; size: number; total: number; pages: number }> {
    const response = await apiClient.get<PaginationResponse<Project>>('/api/v1/projects', {
      ...config,
      params: { ...(params ?? {}), ...(config?.params ?? {}) },
    })

    const { items, pagination } = response.data.data

    return {
      items,
      page: pagination.page,
      size: pagination.size,
      total: pagination.total,
      pages: pagination.pages,
    }
  }

  async getAllProjects(
    filters: Omit<ProjectListParams, 'page' | 'size'> = {},
    config?: AxiosRequestConfig
  ): Promise<Project[]> {
    const projects: Project[] = []
    const seenIds = new Set<string>()
    let page = 1
    while (true) {
      const result = await this.getProjects(
        { ...filters, page, size: PROJECT_OPTION_PAGE_SIZE },
        config
      )
      assertProjectPage(result, page)
      appendDistinctProjects(projects, seenIds, result.items)
      if (projects.length === result.total) return projects
      if (projects.length > result.total) {
        throw new Error(
          `项目分页条目超过声明总数: received=${projects.length}, total=${result.total}`
        )
      }
      if (result.items.length === 0) {
        throw new Error(`项目分页响应提前结束: received=${projects.length}, total=${result.total}`)
      }
      if (page >= result.pages) {
        throw new Error(
          `项目分页页数不足: page=${page}, pages=${result.pages}, total=${result.total}`
        )
      }
      page += 1
    }
  }

  // 获取项目详情
  async getProject(id: string): Promise<Project> {
    return this.get<Project>(`/${id}`)
  }

  // 创建项目
  async createProject(data: ProjectCreateRequest): Promise<Project> {
    return this.uploadFile<Project>('', createProjectFormData(data))
  }

  // 更新项目
  async updateProject(id: string, data: ProjectUpdateRequest): Promise<Project> {
    return this.put<Project>(`/${id}`, data)
  }

  // 更新规则项目配置
  async updateRuleConfig(id: string, data: Partial<ProjectUpdateRequest>): Promise<Project> {
    return this.put<Project>(`/${id}/rule-config`, buildRuleConfigPayload(data))
  }

  // 更新代码项目配置
  async updateCodeConfig(id: string, data: ProjectCodeConfigUpdateRequest): Promise<Project> {
    return this.put<Project>(`/${id}/code-config`, data)
  }

  async getProjectSource(id: string): Promise<ProjectSourceInfo> {
    return this.get<ProjectSourceInfo>(`/${id}/source`)
  }

  async updateProjectSource(id: string, data: ProjectSourcePayload): Promise<ProjectSourceInfo> {
    return this.put<ProjectSourceInfo>(`/${id}/source`, data)
  }

  // 更新文件项目配置
  async updateFileConfig(id: string, data: ProjectFileConfigUpdateRequest): Promise<Project> {
    const response = await apiClient.put(
      `${this.basePath}/${id}/file-config`,
      createFileConfigFormData(data),
      { headers: { 'Content-Type': 'multipart/form-data' } }
    )
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
    const response = await apiClient.post(`/api/v1/projects/${id}/export`, config, {
      responseType: 'blob',
    })
    return response.data
  }

  // 验证项目配置
  async validateProject(
    data: ProjectCreateRequest
  ): Promise<{ valid: boolean; errors?: string[] }> {
    return this.post<{ valid: boolean; errors?: string[] }>('/validate', data)
  }

  // 测试项目连接（规则项目）
  async testProjectConnection(
    id: string
  ): Promise<{ success: boolean; message: string; data?: Record<string, unknown> }> {
    return this.post<{ success: boolean; message: string; data?: Record<string, unknown> }>(
      `/${id}/test-connection`
    )
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
