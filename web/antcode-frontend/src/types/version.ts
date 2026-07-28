/**
 * 项目版本管理类型定义
 */

// 版本信息
export interface ProjectVersion {
  version: number
  version_id: string
  created_at: string
  created_by?: number
  description?: string
  file_count: number
  total_size: number
  content_hash: string
}

// 版本列表响应
export interface VersionListResponse {
  versions: ProjectVersion[]
  total: number
}

// 编辑状态
export interface EditStatus {
  dirty: boolean
  dirty_files_count: number
  last_edit_at?: string
  last_editor_id?: number
  published_version: number
}

// 发布请求
export interface PublishRequest {
  description?: string
}

// 发布响应
export interface PublishResponse {
  version: number
  version_id: string
  artifact_key: string
  file_count: number
  total_size: number
}

// 回滚请求
export interface RollbackRequest {
  version: number
}

// 文件内容响应（带 ETag）
export interface FileContentWithEtag {
  name: string
  path: string
  size: number
  content?: string
  encoding?: string
  etag: string
  mime_type?: string
  is_text: boolean
}

// 文件更新请求
export interface FileUpdateRequest {
  path: string
  content: string
  encoding?: string
}
