import apiClient, {
  refreshSessionToken,
  requestSessionLogin,
  requestSessionLogout,
  TokenManager,
} from './api'
import { isAxiosError } from 'axios'
import {
  broadcastAuthEvent,
  clearSessionHint,
  getSessionGeneration,
  setSessionHint,
} from './authToken'
import { AuthHandler } from '@/utils/authHandler'
import { API_BASE_URL, STORAGE_KEYS } from '@/utils/constants'
import { useAuthStore } from '@/stores/authStore'
import { encryptLoginPassword } from '@/utils/loginEncryption'
import Logger from '@/utils/logger'
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

    const encrypted = await encryptLoginPassword(credentials.password)
    const loginPayload: {
      username: string
      encrypted_password: string
      encryption: string
      key_id: string
    } = {
      username,
      encrypted_password: encrypted.encryptedPassword,
      encryption: encrypted.algorithm,
      key_id: encrypted.keyId,
    }

    const response = await requestSessionLogin<ApiResponse<BackendLoginResponse>>(loginPayload)

    const allowedSource = this.resolveClientEndpoint(API_BASE_URL)

    localStorage.setItem(STORAGE_KEYS.INSTALL_KEY_ALLOWED_SOURCE, allowedSource)

    const payload = response.data.data
    const user = payload.user

    // requestSessionLogin 已在跨标签锁内安装并广播 access token。
    // 记录“存在会话”提示标记（不含敏感信息），供应用启动时决定是否尝试恢复会话
    setSessionHint()

    return {
      access_token: payload.access_token,
      token_type: payload.token_type,
      expires_in: payload.expires_in ?? 3600,
      user
    }
  }

  // 用户登出
  async logout(): Promise<void> {
    await requestSessionLogout()
    AuthHandler.clearAuthData()
  }

  // 获取当前用户信息
  async getCurrentUser(): Promise<User> {
    const resp = await apiClient.get<{ data: User }>('/api/v1/auth/me')
    const user: User | undefined = (resp as unknown as { data?: { data?: User } })?.data?.data
    if (!user) throw new Error('服务端未返回当前用户信息')
    return user
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
    // 后端在改密成功时撤销该用户全部会话，当前 refresh cookie 也已失效。
    // 不再尝试刷新一个必然失败的会话，而是明确清理当前页并通知其他标签。
    clearSessionHint()
    AuthHandler.handleAuthFailure(false)
    broadcastAuthEvent('logout')
  }

  // 刷新 Token
  async refreshToken(): Promise<LoginResponse> {
    const payload = await refreshSessionToken() as BackendLoginResponse
    const user = payload.user ?? await this.getCurrentUser()

    return {
      access_token: payload.access_token,
      token_type: payload.token_type,
      expires_in: payload.expires_in ?? 3600,
      user,
    }
  }

  async restoreSession(): Promise<User> {
    if (this.isAuthenticated()) {
      return this.getCurrentUser()
    }
    return (await this.refreshToken()).user
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

  // 获取当前进程内的权威用户状态。
  getUserInfo(): User | null {
    return useAuthStore.getState().user
  }

  // 获取用户权限
  async getUserPermissions(): Promise<string[]> {
    const response = await apiClient.get<ApiResponse<{ permissions: string[] }>>(
      '/api/v1/auth/permissions'
    )
    const permissions = response.data.data?.permissions
    if (!Array.isArray(permissions)) {
      throw new Error('服务端未返回用户权限')
    }
    return permissions
  }

  // 检查用户是否有特定权限
  hasPermission(permission: string, userPermissions?: string[]): boolean {
    const permissions = userPermissions || this.getCachedPermissions()
    return permissions.includes(permission)
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

    try {
      const payload = TokenManager.getTokenPayload(token)
      if (payload?.exp && payload.exp * 1000 - Date.now() < 5 * 60 * 1000) {
        await this.refreshToken()
      }
      await this.reloadAuthoritativeState()
    } catch (error) {
      this.handleAutoRefreshError(error)
    }
  }

  private async reloadAuthoritativeState(): Promise<void> {
    const start = useAuthStore.getState()
    const startEpoch = start.authEpoch
    const generation = getSessionGeneration()
    const [user, permissions] = await Promise.all([
      this.getCurrentUser(),
      this.getUserPermissions(),
    ])
    const store = useAuthStore.getState()
    if (store.authEpoch !== startEpoch) return
    if (getSessionGeneration() !== generation) return
    store.setPermissions(permissions)
    store.setUser(user)
  }

  private handleAutoRefreshError(error: unknown): void {
    Logger.warn('Auto refresh token failed:', error)
    const status = isAxiosError(error) ? error.response?.status : undefined
    if (status === 401 || status === 403) {
      clearSessionHint()
      AuthHandler.handleAuthFailure(false)
      broadcastAuthEvent('logout')
    }
  }
}

export const authService = new AuthService()
export default authService
