// API 响应基础类型
export interface ApiResponse<T = unknown> {
  success: boolean
  code: number
  message: string
  data: T
  timestamp: string
}

export interface PaginationInfo {
  page: number
  size: number
  total: number
  pages: number
}

export interface PaginationData<T> {
  items: T[]
  pagination: PaginationInfo
}

// 分页响应类型（单一响应信封）
export type PaginationResponse<T> = ApiResponse<PaginationData<T>>

// 兼容别名（与 PaginationResponse 结构一致）
export type ApiPaginatedResponse<T> = PaginationResponse<T>

export interface ApiErrorDetail {
  field: string
  message: string
}

export interface ApiErrorData {
  error_code?: string
  errors: ApiErrorDetail[]
}

// 错误响应类型
export type ApiError = ApiResponse<ApiErrorData | null>

// 请求配置类型
export interface RequestConfig {
  timeout?: number
  headers?: Record<string, string>
  params?: Record<string, unknown>
}

// 上传文件响应
export interface UploadResponse {
  success: boolean
  data: {
    filename: string
    original_name: string
    file_path: string
    file_size: number
    file_hash: string
  }
}

// 认证相关类型
export interface LoginRequest {
  username: string
  password: string
}

export interface LoginPublicKeyResponse {
  algorithm: string
  key_id: string
  public_key: string
}

export interface LoginResponse {
  access_token: string
  refresh_token?: string
  token_type: string
  expires_in: number
  user: User
}

// 后端实际返回的登录响应格式
export interface BackendLoginResponse {
  access_token: string
  refresh_token?: string
  token_type: string
  expires_in?: number
  user: User  // 嵌套的用户对象
}

export interface User {
  id: string  // public_id
  username: string
  email?: string
  is_active: boolean
  is_admin: boolean
  is_super_admin?: boolean
  created_at: string
  updated_at: string
  last_login_at?: string
  is_online?: boolean
}

// Token 信息
export interface TokenInfo {
  access_token: string
  refresh_token?: string
  expires_at: number
  user: User
}

// 刷新Token请求
export interface RefreshTokenRequest {
  refresh_token: string
}

// 用户更新请求
export interface UpdateUserRequest {
  email?: string
  password?: string
  current_password?: string
}

// 系统信息
export interface SystemInfo {
  version: string
  python_version: string
  platform: string
  cpu_count: number
  memory_total: number
  memory_available: number
  disk_total: number
  disk_free: number
  uptime: number
}

// 健康检查响应
export interface HealthCheckResponse {
  status: 'healthy' | 'unhealthy'
  timestamp: string
  version: string
  database: {
    status: 'connected' | 'disconnected'
    response_time?: number
  }
  redis?: {
    status: 'connected' | 'disconnected'
    response_time?: number
  }
}
