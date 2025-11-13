// API 响应基础类型
export interface ApiResponse<T = unknown> {
  success: boolean
  data: T
  message?: string
  code?: number
}

// 分页响应类型
export interface PaginatedResponse<T> {
  success: boolean
  data: {
    items: T[]
    total: number
    page: number
    size: number
    pages: number
  }
}

// 错误响应类型
export interface ApiError {
  detail: string
  code?: string
  field?: string
}

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

export interface LoginResponse {
  access_token: string
  token_type: string
  expires_in: number
  user: User
}

// 后端实际返回的登录响应格式
export interface BackendLoginResponse {
  access_token: string
  token_type: string
  user_id: number
  username: string
  is_admin: boolean
}

export interface User {
  id: number
  username: string
  email?: string
  is_active: boolean
  is_admin: boolean
  created_at: string
  updated_at: string
  last_login_at?: string
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

// 用户注册请求
export interface RegisterRequest {
  username: string
  password: string
  email?: string
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
