import { useState, useEffect, useCallback } from 'react'
import { useNavigate } from 'react-router-dom'
// 提示交由全局拦截器与后端 message 处理
import { authService } from '@/services/auth'
import { useAuth as useAuthStore } from '@/stores/authStore'
import { AuthHandler } from '@/utils/authHandler'
import Logger from '@/utils/logger'
import type { LoginRequest, RegisterRequest, UpdateUserRequest } from '@/types'

export const useAuth = () => {
  const navigate = useNavigate()
  const [loading, setLoading] = useState(false)
  
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
    updateUser: updateStoreUser,
    hasPermission,
    hasAnyPermission,
    hasAllPermissions
  } = useAuthStore()

  // 登录
  const login = useCallback(async (credentials: LoginRequest) => {
    setLoading(true)
    setStoreLoading(true)
    setError(null)

    try {
      const response = await authService.login(credentials)
      setUser(response.user)
      
      // 获取用户权限 - 暂时注释，后端未实现该接口
      // try {
      //   const userPermissions = await authService.getUserPermissions()
      //   setPermissions(userPermissions)
      // } catch (permError) {
      //   Logger.warn('获取用户权限失败:', permError)
      // }

      // 成功提示交由拦截器/后端 message 处理
      return response
    } catch (error: any) {
      const errorMessage = error.response?.data?.detail || error.message || '登录失败'
      setError(errorMessage)
      throw error
    } finally {
      setLoading(false)
      setStoreLoading(false)
    }
  }, [setUser, setStoreLoading, setError, setPermissions])

  // 注册
  const register = useCallback(async (userData: RegisterRequest) => {
    setLoading(true)
    setStoreLoading(true)
    setError(null)

    try {
      const response = await authService.register(userData)
      return response
    } catch (error: any) {
      const errorMessage = error.response?.data?.detail || error.message || '注册失败'
      setError(errorMessage)
      throw error
    } finally {
      setLoading(false)
      setStoreLoading(false)
    }
  }, [setStoreLoading, setError])

  // 登出
  const logout = useCallback(async () => {
    setLoading(true)
    setStoreLoading(true)

    try {
      await authService.logout()
      clearUser()
      navigate('/login')
    } catch (error: any) {
      Logger.warn('登出请求失败:', error)
      // 即使登出请求失败，也要清除本地状态
      clearUser()
      navigate('/login')
    } finally {
      setLoading(false)
      setStoreLoading(false)
    }
  }, [clearUser, navigate, setStoreLoading])

  // 获取当前用户信息
  const getCurrentUser = useCallback(async () => {
    setLoading(true)
    setStoreLoading(true)
    setError(null)

    try {
      const userData = await authService.getCurrentUser()
      setUser(userData)
      
      // 同时获取权限 - 暂时注释，后端未实现该接口
      // try {
      //   const userPermissions = await authService.getUserPermissions()
      //   setPermissions(userPermissions)
      // } catch (permError) {
      //   Logger.warn('获取用户权限失败:', permError)
      // }

      return userData
    } catch (error: any) {
      const errorMessage = error.response?.data?.detail || error.message || '获取用户信息失败'
      setError(errorMessage)
      
      // 如果是认证错误，使用统一的认证处理
      if (AuthHandler.isAuthError(error)) {
        clearUser()
        AuthHandler.handleAuthFailure(false) // 不显示消息，因为上面已经设置了错误
      }
      
      throw error
    } finally {
      setLoading(false)
      setStoreLoading(false)
    }
  }, [setUser, setStoreLoading, setError, setPermissions, clearUser, navigate])

  // 更新用户信息
  const updateUser = useCallback(async (userData: UpdateUserRequest) => {
    setLoading(true)
    setStoreLoading(true)
    setError(null)

    try {
      const updatedUser = await authService.updateUser(userData)
      updateStoreUser(updatedUser)
      return updatedUser
    } catch (error: any) {
      const errorMessage = error.response?.data?.detail || error.message || '更新用户信息失败'
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
    } catch (error: any) {
      const errorMessage = error.response?.data?.detail || error.message || '密码修改失败'
      setError(errorMessage)
      throw error
    } finally {
      setLoading(false)
      setStoreLoading(false)
    }
  }, [setStoreLoading, setError])

  // 检查登录状态
  const checkAuth = useCallback(async () => {
    setLoading(true)
    try {
      if (authService.isAuthenticated()) {
        // 从本地存储获取用户信息，不调用API
        const userInfo = authService.getUserInfo()
        if (userInfo) {
          setUser(userInfo)
        }
      }
    } catch (error) {
      Logger.warn('检查登录状态失败:', error)
      // 如果本地存储的用户信息有问题，清除认证状态
      clearUser()
    } finally {
      setLoading(false)
    }
  }, [setUser, clearUser, setLoading])

  // 刷新Token
  const refreshToken = useCallback(async () => {
    try {
      const response = await authService.refreshToken()
      setUser(response.user)
      return response
    } catch (error: any) {
      Logger.warn('刷新Token失败:', error)
      clearUser()
      AuthHandler.handleAuthFailure()
      throw error
    }
  }, [setUser, clearUser, navigate])

  // 检查用户名可用性
  const checkUsernameAvailability = useCallback(async (username: string) => {
    try {
      return await authService.checkUsernameAvailability(username)
    } catch (error: any) {
      Logger.warn('检查用户名可用性失败:', error)
      return false
    }
  }, [])

  // 检查邮箱可用性
  const checkEmailAvailability = useCallback(async (email: string) => {
    try {
      return await authService.checkEmailAvailability(email)
    } catch (error: any) {
      Logger.warn('检查邮箱可用性失败:', error)
      return false
    }
  }, [])

  // 发送重置密码邮件
  const sendResetPasswordEmail = useCallback(async (email: string) => {
    setLoading(true)
    setError(null)

    try {
      await authService.sendResetPasswordEmail(email)
    } catch (error: any) {
      const errorMessage = error.response?.data?.detail || error.message || '发送邮件失败'
      setError(errorMessage)
      throw error
    } finally {
      setLoading(false)
    }
  }, [setError])

  // 重置密码
  const resetPassword = useCallback(async (token: string, newPassword: string) => {
    setLoading(true)
    setError(null)

    try {
      await authService.resetPassword(token, newPassword)
    } catch (error: any) {
      const errorMessage = error.response?.data?.detail || error.message || '密码重置失败'
      setError(errorMessage)
      throw error
    } finally {
      setLoading(false)
    }
  }, [setError])

  // 组件挂载时检查登录状态
  useEffect(() => {
    checkAuth()
  }, [checkAuth])

  // 自动刷新Token
  useEffect(() => {
    if (isAuthenticated) {
      const interval = setInterval(() => {
        authService.autoRefreshToken()
      }, 5 * 60 * 1000) // 每5分钟检查一次

      return () => clearInterval(interval)
    }
  }, [isAuthenticated])

  return {
    // 状态
    user,
    isAuthenticated,
    loading,
    error,
    permissions,

    // 方法
    login,
    register,
    logout,
    getCurrentUser,
    updateUser,
    changePassword,
    checkAuth,
    refreshToken,
    checkUsernameAvailability,
    checkEmailAvailability,
    sendResetPasswordEmail,
    resetPassword,

    // 权限检查
    hasPermission,
    hasAnyPermission,
    hasAllPermissions
  }
}

export default useAuth
