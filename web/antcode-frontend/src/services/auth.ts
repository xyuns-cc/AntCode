import apiClient, { TokenManager } from './api'
import { AuthHandler } from '@/utils/authHandler'
import { API_BASE_URL, STORAGE_KEYS } from '@/utils/constants'
import Logger from '@/utils/logger'
import { encryptLoginPassword } from '@/utils/loginEncryption'
import type {
  LoginRequest,
  LoginResponse,
  BackendLoginResponse,
  User,
  UpdateUserRequest,
  ApiResponse
} from '@/types'

class AuthService {
  // 用户登录
  async login(credentials: LoginRequest): Promise<LoginResponse> {
    const username = credentials.username.trim()
    if (!username) {
      throw new Error('用户名不能为空')
    }

    let loginPayload: {
      username: string
      password?: string
      encrypted_password?: string
      encryption?: string
      key_id?: string
    } = {
      username,
      password: credentials.password,
    }

    try {
      const encrypted = await encryptLoginPassword(credentials.password)
      loginPayload = {
        username,
        encrypted_password: encrypted.encryptedPassword,
        encryption: encrypted.algorithm,
        key_id: encrypted.keyId,
      }
    } catch (error) {
      Logger.warn('登录密码加密失败，回退明文登录:', error)
    }

    const response = await apiClient.post<ApiResponse<BackendLoginResponse>>('/api/v1/auth/login', loginPayload)

    const allowedSource = this.resolveClientEndpoint(API_BASE_URL)

    localStorage.setItem(STORAGE_KEYS.INSTALL_KEY_ALLOWED_SOURCE, allowedSource)

    const payload = response.data.data
    const user = {
      ...payload.user,
      // 当前后端未单独下发 super_admin 字段，这里按用户名兜底
      is_super_admin: payload.user.username === 'admin',
    }

    // 保存 token 和用户信息
    TokenManager.setTokens(payload.access_token, payload.refresh_token)
    localStorage.setItem(STORAGE_KEYS.USER_INFO, JSON.stringify(user))

    return {
      access_token: payload.access_token,
      refresh_token: payload.refresh_token,
      token_type: payload.token_type,
      expires_in: payload.expires_in ?? 3600,
      user
    }
  }

  // 用户登出
  async logout(): Promise<void> {
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

    const response = await apiClient.post<ApiResponse<BackendLoginResponse>>('/api/v1/auth/refresh', {
      refresh_token: refreshToken,
    })

    const payload = response.data.data
    const user = {
      ...payload.user,
      is_super_admin: payload.user.username === 'admin',
    }

    // 更新 token
    TokenManager.setTokens(payload.access_token, payload.refresh_token)
    localStorage.setItem(STORAGE_KEYS.USER_INFO, JSON.stringify(user))

    return {
      access_token: payload.access_token,
      refresh_token: payload.refresh_token,
      token_type: payload.token_type,
      expires_in: payload.expires_in ?? 3600,
      user,
    }
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

  private resolveClientEndpoint(baseUrl: string): string {
    try {
      const parsed = new URL(baseUrl)
      const host = parsed.hostname
      if (host) {
        return host
      }
    } catch {
      // ignore
    }

    if (typeof window !== 'undefined' && window.location?.hostname) {
      return window.location.hostname
    }
    return 'unknown'
  }

  // 自动刷新 token
  async autoRefreshToken(): Promise<void> {
    const token = TokenManager.getAccessToken()
    if (!token) return

    // 如果 token 即将过期（5分钟内），尝试刷新
    const payload = TokenManager.getTokenPayload(token)
    if (payload?.exp && payload.exp * 1000 - Date.now() < 5 * 60 * 1000) {
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
