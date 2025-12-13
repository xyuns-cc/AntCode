import apiClient, { TokenManager } from './api'
import { AuthHandler } from '@/utils/authHandler'
import { STORAGE_KEYS } from '@/utils/constants'
import Logger from '@/utils/logger'
import type {
  LoginRequest,
  LoginResponse,
  BackendLoginResponse,
  User,
  RegisterRequest,
  UpdateUserRequest,
  ApiResponse
} from '@/types'

class AuthService {
  // 用户登录
  async login(credentials: LoginRequest): Promise<LoginResponse> {
    const response = await apiClient.post<ApiResponse<BackendLoginResponse>>('/api/v1/auth/login', {
      username: credentials.username,
      password: credentials.password
    })

    // 保存 token 和用户信息
    TokenManager.setTokens(response.data.data.access_token)

    // 构造用户对象（后端返回的格式可能不同）
    const user = {
      id: response.data.data.user_id,
      username: response.data.data.username,
      email: '',
      is_active: true,
      is_admin: response.data.data.is_admin, // 添加管理员标识
      created_at: new Date().toISOString(),
      updated_at: new Date().toISOString()
    }

    localStorage.setItem(STORAGE_KEYS.USER_INFO, JSON.stringify(user))

    return {
      access_token: response.data.data.access_token,
      token_type: response.data.data.token_type,
      expires_in: 3600, // 默认1小时
      user
    }
  }

  // 用户注册
  async register(userData: RegisterRequest): Promise<ApiResponse<User>> {
    const response = await apiClient.post<ApiResponse<User>>('/api/v1/auth/register', userData)
    return response.data
  }

  // 用户登出
  async logout(): Promise<void> {
    // 后端暂未提供 logout 接口，直接清理本地认证数据
    AuthHandler.clearAuthData()
  }

  // 获取当前用户信息
  async getCurrentUser(): Promise<User> {
    // 由于后端没有 /auth/me 端点，我们从本地存储获取用户信息
    const userInfo = localStorage.getItem(STORAGE_KEYS.USER_INFO)
    if (!userInfo) {
      throw new Error('用户信息不存在')
    }

    try {
      return JSON.parse(userInfo)
    } catch {
      throw new Error('用户信息格式错误')
    }
  }

  // 更新用户信息
  async updateUser(userData: UpdateUserRequest): Promise<User> {
    // 获取当前用户ID
    const userInfo = this.getUserInfo()
    if (!userInfo) {
      throw new Error('用户未登录')
    }

    const response = await apiClient.put<ApiResponse<User>>(`/api/v1/users/${userInfo.id}`, userData)

    // 更新本地存储的用户信息
    localStorage.setItem(STORAGE_KEYS.USER_INFO, JSON.stringify(response.data.data))

    return response.data.data
  }

  // 修改密码
  async changePassword(currentPassword: string, newPassword: string): Promise<void> {
    // 获取当前用户ID
    const userInfo = this.getUserInfo()
    if (!userInfo) {
      throw new Error('用户未登录')
    }

    await apiClient.put(`/api/v1/users/${userInfo.id}/password`, {
      old_password: currentPassword,
      new_password: newPassword,
    })
  }

  // 刷新 Token
  async refreshToken(): Promise<LoginResponse> {
    const refreshToken = TokenManager.getRefreshToken()
    if (!refreshToken) {
      throw new Error('No refresh token available')
    }

    const response = await apiClient.post<LoginResponse>('/api/v1/auth/refresh', {
      refresh_token: refreshToken,
    })

    // 更新 token
    TokenManager.setTokens(response.data.access_token)
    localStorage.setItem(STORAGE_KEYS.USER_INFO, JSON.stringify(response.data.user))

    return response.data
  }

  // 检查是否已登录
  isAuthenticated(): boolean {
    const token = TokenManager.getAccessToken()
    if (!token) return false

    // 检查 token 是否过期
    if (TokenManager.isTokenExpired(token)) {
      TokenManager.clearTokens()
      return false
    }

    return true
  }

  // 获取 token
  getToken(): string | null {
    return TokenManager.getAccessToken()
  }

  // 获取用户信息（从本地存储）
  getUserInfo(): User | null {
    const userInfo = localStorage.getItem(STORAGE_KEYS.USER_INFO)
    if (!userInfo) return null

    try {
      return JSON.parse(userInfo)
    } catch {
      return null
    }
  }

  // 验证邮箱
  async verifyEmail(token: string): Promise<void> {
    await apiClient.post('/api/v1/auth/verify-email', { token })
  }

  // 发送重置密码邮件
  async sendResetPasswordEmail(email: string): Promise<void> {
    await apiClient.post('/api/v1/auth/forgot-password', { email })
  }

  // 重置密码
  async resetPassword(token: string, newPassword: string): Promise<void> {
    await apiClient.post('/api/v1/auth/reset-password', {
      token,
      new_password: newPassword,
    })
  }

  // 检查用户名是否可用
  async checkUsernameAvailability(username: string): Promise<boolean> {
    const response = await apiClient.get<ApiResponse<{ available: boolean }>>(
      `/api/v1/auth/check-username/${username}`
    )
    return response.data.data.available
  }

  // 检查邮箱是否可用
  async checkEmailAvailability(email: string): Promise<boolean> {
    const response = await apiClient.get<ApiResponse<{ available: boolean }>>(
      `/api/v1/auth/check-email/${email}`
    )
    return response.data.data.available
  }

  // 获取用户权限
  async getUserPermissions(): Promise<string[]> {
    const response = await apiClient.get<ApiResponse<{ permissions: string[] }>>(
      '/api/v1/auth/permissions'
    )
    return response.data.data.permissions
  }

  // 检查用户是否有特定权限
  hasPermission(permission: string, userPermissions?: string[]): boolean {
    const permissions = userPermissions || this.getCachedPermissions()
    return permissions.includes(permission) || permissions.includes('admin')
  }

  // 获取缓存的权限（从 token 中解析）
  private getCachedPermissions(): string[] {
    const token = TokenManager.getAccessToken()
    if (!token) return []

    const payload = TokenManager.getTokenPayload(token)
    return payload?.permissions || []
  }

  // 自动刷新 token
  async autoRefreshToken(): Promise<void> {
    const token = TokenManager.getAccessToken()
    if (!token) return

    // 如果 token 即将过期（5分钟内），尝试刷新
    const payload = TokenManager.getTokenPayload(token)
    if (payload && payload.exp * 1000 - Date.now() < 5 * 60 * 1000) {
      try {
        await this.refreshToken()
      } catch (error) {
        Logger.warn('Auto refresh token failed:', error)
        TokenManager.clearTokens()
      }
    }
  }
}

export const authService = new AuthService()
export default authService
