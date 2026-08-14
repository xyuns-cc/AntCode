import { useState, useEffect, useCallback } from 'react'
import { useNavigate } from 'react-router'
import { isAxiosError } from 'axios'
// 提示交由全局拦截器与后端 message 处理
import { authService } from '@/services/auth'
import { useAuth as useAuthState } from '@/stores/authStore'
import {
  broadcastAuthEvent,
  clearSessionHint,
  decodeAccessToken,
  getAccessToken,
  getSessionGeneration,
} from '@/services/authToken'
import { AuthAccountChangedError } from '@/services/authRefreshCoordinator'
import {
  ensureSessionRestored,
  isSessionRestoreSettled,
} from '@/services/authSessionRestore'
import { AuthHandler } from '@/utils/authHandler'
import Logger from '@/utils/logger'
import type { LoginRequest, UpdateUserRequest } from '@/types'

const extractErrorMessage = (error: unknown, fallback: string) => {
  if (isAxiosError(error)) {
    const detail = (error.response?.data as { detail?: string } | undefined)?.detail
    return detail || error.message || fallback
  }
  if (error instanceof Error) {
    return error.message || fallback
  }
  return fallback
}

const isCurrentLoginSession = (sessionJti: string, username: string): boolean => {
  if (getSessionGeneration() !== sessionJti) return false
  const token = getAccessToken()
  if (!token) return false
  const identity = decodeAccessToken(token)
  return identity?.session_jti === sessionJti && identity.username === username
}

export { resetSessionRestore } from '@/services/authSessionRestore'

export const useAuth = () => {
  const navigate = useNavigate()
  const [loading, setLoading] = useState(() => !isSessionRestoreSettled())

  const {
    user,
    isAuthenticated,
    error,
    permissions,
    setUser,
    setLoading: setStoreLoading,
    setError,
    setPermissions,
    clearUser,
    updateUser: updateStoreUser
  } = useAuthState()

  // 登录
  const login = useCallback(async (credentials: LoginRequest) => {
    setLoading(true)
    setStoreLoading(true)
    setError(null)

    let establishedGeneration: string | null = null
    try {
      const response = await authService.login(credentials)
      const identity = decodeAccessToken(response.access_token)
      const sessionJti = identity?.session_jti
      establishedGeneration = typeof sessionJti === 'string' ? sessionJti : null
      if (!establishedGeneration || identity?.username !== response.user.username) {
        throw new Error('登录响应中的会话身份不一致')
      }

      const userPermissions = await authService.getUserPermissions()
      if (!isCurrentLoginSession(establishedGeneration, response.user.username)) {
        throw new AuthAccountChangedError()
      }
      setPermissions(userPermissions)
      setUser(response.user)
      broadcastAuthEvent('login', response.user.username)

      // 成功提示交由拦截器/后端 message 处理
      return response
    } catch (error: unknown) {
      const errorMessage = extractErrorMessage(error, '登录失败')
      const ownsEstablishedSession = Boolean(
        establishedGeneration && getSessionGeneration() === establishedGeneration,
      )
      if (ownsEstablishedSession) {
        try {
          await authService.logout()
        } catch (logoutError: unknown) {
          const logoutMessage = extractErrorMessage(logoutError, '撤销不完整会话失败')
          setError(`${errorMessage}；${logoutMessage}`)
          throw logoutError
        }
        clearSessionHint()
        clearUser()
        broadcastAuthEvent('logout')
      }
      if (!establishedGeneration || ownsEstablishedSession) setError(errorMessage)
      throw error
    } finally {
      setLoading(false)
      setStoreLoading(false)
    }
  }, [clearUser, setUser, setStoreLoading, setError, setPermissions])

  // 登出
  const logout = useCallback(async () => {
    setLoading(true)
    setStoreLoading(true)

    try {
      await authService.logout()
      clearSessionHint()
      clearUser()
      broadcastAuthEvent('logout')
      navigate('/login')
    } catch (error: unknown) {
      Logger.warn('登出请求失败:', error)
      setError(extractErrorMessage(error, '登出失败'))
      throw error
    } finally {
      setLoading(false)
      setStoreLoading(false)
    }
  }, [clearUser, navigate, setError, setStoreLoading])

  // 获取当前用户信息
  const getCurrentUser = useCallback(async () => {
    setLoading(true)
    setStoreLoading(true)
    setError(null)

    try {
      const userData = await authService.getCurrentUser()
      const userPermissions = await authService.getUserPermissions()
      setPermissions(userPermissions)
      setUser(userData)

      return userData
    } catch (error: unknown) {
      const errorMessage = extractErrorMessage(error, '获取用户信息失败')
      // 如果是认证错误，使用统一的认证处理
      if (AuthHandler.isAuthError(error)) {
        clearUser()
        AuthHandler.handleAuthFailure(false) // 不显示消息，因为上面已经设置了错误
      }
      setError(errorMessage)
      throw error
    } finally {
      setLoading(false)
      setStoreLoading(false)
    }
  }, [setUser, setStoreLoading, setError, setPermissions, clearUser])

  // 更新用户信息
  const updateUser = useCallback(async (userData: UpdateUserRequest) => {
    setLoading(true)
    setStoreLoading(true)
    setError(null)

    try {
      const updatedUser = await authService.updateUser(userData)
      updateStoreUser(updatedUser)
      return updatedUser
    } catch (error: unknown) {
      const errorMessage = extractErrorMessage(error, '更新用户信息失败')
      setError(errorMessage)
      throw error
    } finally {
      setLoading(false)
      setStoreLoading(false)
    }
  }, [updateStoreUser, setStoreLoading, setError])

  // 修改密码
  const changePassword = useCallback(async (currentPassword: string, newPassword: string) => {
    setLoading(true)
    setStoreLoading(true)
    setError(null)

    try {
      await authService.changePassword(currentPassword, newPassword)
    } catch (error: unknown) {
      const errorMessage = extractErrorMessage(error, '密码修改失败')
      setError(errorMessage)
      throw error
    } finally {
      setLoading(false)
      setStoreLoading(false)
    }
  }, [setStoreLoading, setError])

  // 手动检查登录状态（强制重新执行会话恢复）
  const checkAuth = useCallback(async () => {
    setLoading(true)
    setError(null)
    try {
      await ensureSessionRestored({ force: true })
    } finally {
      setLoading(false)
    }
  }, [setError])

  // 刷新Token
  const refreshToken = useCallback(async () => {
    try {
      const response = await authService.refreshToken()
      const userPermissions = await authService.getUserPermissions()
      setPermissions(userPermissions)
      setUser(response.user)
      return response
    } catch (error: unknown) {
      Logger.warn('刷新Token失败:', error)
      clearUser()
      AuthHandler.handleAuthFailure()
      setError(extractErrorMessage(error, '刷新Token失败'))
      throw error
    }
  }, [setUser, setError, setPermissions, clearUser])

  // 组件挂载时确保启动会话恢复已完成（模块级单飞，整个应用只执行一次）
  useEffect(() => {
    let active = true
    ensureSessionRestored()
      .catch(() => undefined)
      .finally(() => {
        if (active) setLoading(false)
      })
    return () => {
      active = false
    }
  }, [])

  return {
    // 状态
    user,
    isAuthenticated,
    loading,
    error,
    permissions,

    // 方法
    login,
    logout,
    getCurrentUser,
    updateUser,
    changePassword,
    checkAuth,
    refreshToken,
    clearUser
  }
}

export default useAuth
