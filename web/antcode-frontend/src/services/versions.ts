/**
 * 项目版本管理服务
 */
import { BaseService } from './base'
import Logger from '@/utils/logger'
import type {
  EditStatus,
  PublishRequest,
  PublishResponse,
  RollbackRequest,
  VersionListResponse,
  FileContentWithEtag,
  FileUpdateRequest,
} from '@/types/version'

class VersionService extends BaseService {
  constructor() {
    super('/api/v1/projects')
  }

  /**
   * 发布草稿为新版本
   */
  async publish(projectId: string, request?: PublishRequest): Promise<PublishResponse> {
    try {
      return await this.post<PublishResponse>(`/${projectId}/publish`, request || {})
    } catch (error) {
      Logger.error('发布版本失败:', error)
      throw error
    }
  }

  /**
   * 丢弃草稿修改
   */
  async discard(projectId: string): Promise<void> {
    try {
      await this.post(`/${projectId}/discard`)
    } catch (error) {
      Logger.error('丢弃草稿失败:', error)
      throw error
    }
  }

  /**
   * 获取版本列表
   */
  async getVersions(projectId: string): Promise<VersionListResponse> {
    try {
      return await this.get<VersionListResponse>(`/${projectId}/versions`)
    } catch (error) {
      Logger.error('获取版本列表失败:', error)
      throw error
    }
  }

  /**
   * 回滚到指定版本
   */
  async rollback(projectId: string, request: RollbackRequest): Promise<void> {
    try {
      await this.post(`/${projectId}/rollback`, request)
    } catch (error) {
      Logger.error('回滚版本失败:', error)
      throw error
    }
  }

  /**
   * 获取编辑状态
   */
  async getEditStatus(projectId: string): Promise<EditStatus> {
    try {
      return await this.get<EditStatus>(`/${projectId}/edit-status`)
    } catch (error) {
      Logger.error('获取编辑状态失败:', error)
      throw error
    }
  }

  /**
   * 获取草稿文件内容（带 ETag）
   */
  async getDraftFileContent(projectId: string, filePath: string): Promise<FileContentWithEtag> {
    try {
      return await this.get<FileContentWithEtag>(
        `/${projectId}/draft/files/${encodeURIComponent(filePath)}`
      )
    } catch (error) {
      Logger.error('获取草稿文件内容失败:', error)
      throw error
    }
  }

  /**
   * 更新草稿文件内容（带 ETag 并发控制）
   */
  async updateDraftFileContent(
    projectId: string,
    filePath: string,
    request: FileUpdateRequest,
    etag?: string
  ): Promise<FileContentWithEtag> {
    try {
      const headers: Record<string, string> = {}
      if (etag) {
        headers['If-Match'] = etag
      }
      return await this.put<FileContentWithEtag>(
        `/${projectId}/draft/files/${encodeURIComponent(filePath)}`,
        request,
        { headers }
      )
    } catch (error) {
      Logger.error('更新草稿文件内容失败:', error)
      throw error
    }
  }

  /**
   * 删除草稿文件
   */
  async deleteDraftFile(projectId: string, filePath: string, etag?: string): Promise<void> {
    try {
      const headers: Record<string, string> = {}
      if (etag) {
        headers['If-Match'] = etag
      }
      await this.delete(`/${projectId}/draft/files/${encodeURIComponent(filePath)}`, { headers })
    } catch (error) {
      Logger.error('删除草稿文件失败:', error)
      throw error
    }
  }

  /**
   * 移动/重命名草稿文件
   */
  async moveDraftFile(projectId: string, fromPath: string, toPath: string): Promise<void> {
    try {
      await this.post(`/${projectId}/draft/files/move`, { from: fromPath, to: toPath })
    } catch (error) {
      Logger.error('移动文件失败:', error)
      throw error
    }
  }
}

export const versionService = new VersionService()
export default versionService
