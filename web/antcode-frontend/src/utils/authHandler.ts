import showNotification from '@/utils/notification'
import { STORAGE_KEYS } from './constants'
import Logger from './logger'
import { useAuthStore } from '@/stores/authStore'

/**
 * 认证处理工具类
 * 统一处理JWT认证失败的情况
 */
export class AuthHandler {
  private static isRedirecting = false

  /**
   * 处理认证失败
   * 清除本地存储的认证信息并跳转到登录页面
   */
  static handleAuthFailure(showMessage = true): void {
    // 防止重复跳转
    if (this.isRedirecting) {
      return
    }

    Logger.warn('认证失败，清除用户信息并跳转到登录页面')

    // 清除所有认证相关的本地存储
    this.clearAuthData()

    // 清除Zustand store中的用户状态
    this.clearAuthStore()

    // 显示错误消息
    if (showMessage) {
    showNotification('error', '登录已过期，请重新登录')
    }

    // 跳转到登录页面
    this.redirectToLogin()
  }

  /** 清除认证数据（保留记住我数据） */
  static clearAuthData(): void {
    try {
      localStorage.removeItem(STORAGE_KEYS.ACCESS_TOKEN)
      localStorage.removeItem(STORAGE_KEYS.REFRESH_TOKEN)
      localStorage.removeItem(STORAGE_KEYS.USER_INFO)
      
      // 清除其他认证数据，保留 remember_ 前缀的数据
      Object.keys(localStorage)
        .filter(key => !key.startsWith('remember_') && 
          (key.includes('auth') || key.includes('token') || key.includes('user')))
        .forEach(key => localStorage.removeItem(key))

      Logger.info('认证数据已清除')
    } catch (error) {
      Logger.error('清除认证数据失败:', error)
    }
  }

  /**
   * 清除Zustand store中的认证状态
   */
  static clearAuthStore(): void {
    try {
        const { clearUser } = useAuthStore.getState()
        clearUser()
        Logger.info('Zustand认证状态已清除')
    } catch (error) {
      Logger.error('清除Zustand认证状态失败:', error)
    }
  }

  /**
   * 跳转到登录页面
   */
  static redirectToLogin(): void {
    // 防止重复跳转
    if (this.isRedirecting || window.location.pathname === '/login') {
      return
    }

    this.isRedirecting = true

    try {
      // 保存当前页面路径，登录后可以跳转回来
      const currentPath = window.location.pathname + window.location.search
      if (currentPath !== '/login') {
        sessionStorage.setItem('redirectPath', currentPath)
      }

      // 延迟重置标志，避免快速连续的认证失败请求
      setTimeout(() => {
        this.isRedirecting = false
      }, 2000)

      // 使用 replace 避免在浏览器历史中留下记录
      window.location.replace('/login')
    } catch (error) {
      Logger.error('跳转到登录页面失败:', error)
      this.isRedirecting = false
      // 如果跳转失败，尝试刷新页面
      window.location.reload()
    }
  }

  /**
   * 获取登录后的重定向路径
   */
  static getRedirectPath(): string {
    const redirectPath = sessionStorage.getItem('redirectPath')
    sessionStorage.removeItem('redirectPath')
    return redirectPath && redirectPath !== '/login' ? redirectPath : '/dashboard'
  }

  /**
   * 检查是否为认证错误
   */
  static isAuthError(error: unknown): boolean {
    if (!error || typeof error !== 'object' || !('response' in error)) {
      return false
    }

    const response = (error as { response?: { status?: number; data?: { detail?: string; message?: string } } }).response
    if (!response) return false

    const status = response.status
    const message = response.data?.detail || response.data?.message || ''

    // 检查状态码和错误消息
    return (
      status === 401 ||
      message.includes('token') ||
      message.includes('认证') ||
      message.includes('登录') ||
      message.includes('unauthorized') ||
      message.includes('expired')
    )
  }

  /**
   * 处理API错误
   * 如果是认证错误，自动处理；否则返回false让调用方处理
   */
  static handleApiError(error: unknown, showMessage = true): boolean {
    if (this.isAuthError(error)) {
      this.handleAuthFailure(showMessage)
      return true
    }
    return false
  }

  /**
   * 重置重定向状态（用于测试或特殊情况）
   */
  static resetRedirectingState(): void {
    this.isRedirecting = false
  }
}

/**
 * 创建认证错误处理的高阶函数
 */
export function withAuthErrorHandling<Args extends unknown[], ReturnType>(
  fn: (...args: Args) => Promise<ReturnType>,
  showMessage = true
): (...args: Args) => Promise<ReturnType> {
  return (async (...args: Args) => {
    try {
      return await fn(...args)
    } catch (error) {
      if (AuthHandler.handleApiError(error, showMessage)) {
        // 如果是认证错误，已经处理了，抛出一个特殊的错误
        throw new Error('AUTH_FAILURE')
      }
      // 不是认证错误，重新抛出原错误
      throw error
    }
  })
}

export default AuthHandler
