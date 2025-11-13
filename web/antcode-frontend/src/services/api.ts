/**
 * HTTP 客户端配置
 * 统一的axios实例配置，包含请求/响应拦截器
 */

import axios from 'axios'
import type { AxiosInstance, AxiosRequestConfig, AxiosResponse } from 'axios'
import showNotification from '@/utils/notification'
import { API_BASE_URL, STORAGE_KEYS } from '@/utils/constants'
import { AuthHandler } from '@/utils/authHandler'

// 创建 axios 实例
const apiClient: AxiosInstance = axios.create({
  baseURL: API_BASE_URL,
  timeout: 30000,
  headers: {
    'Content-Type': 'application/json',
  },
})

// ==================== 请求拦截器 ====================
apiClient.interceptors.request.use(
  (config) => {
    // 添加 JWT Token
    const token = localStorage.getItem(STORAGE_KEYS.ACCESS_TOKEN)
    if (token && config.headers) {
      config.headers.Authorization = `Bearer ${token}`
    }
    return config
  },
  (error) => {
    return Promise.reject(error)
  }
)

// ==================== 响应拦截器 - 成功处理 ====================
apiClient.interceptors.response.use(
  (response: AxiosResponse) => {
    try {
      const data = response?.data
      const cfg = response?.config || {}
      const skipSuccessToast = cfg?.headers && (cfg.headers as any)['X-Skip-Success-Toast']
      const method = (cfg.method || '').toUpperCase()
      const url = cfg.url || ''
      
      if (data && typeof data === 'object') {
        const msg = data.message ? String(data.message) : ''
        const isMutation = method && method !== 'GET'
        const isNoisyQueryMsg = ['查询成功', '获取成功', '操作成功', '获取', '查询', '成功'].includes(msg)
        
        // 变更操作的白名单 - 这些接口的成功提示会显示
        const mutationWhitelist = [
          /\/api\/v1\/scheduler\/tasks\/(\d+)\/trigger$/,
          /\/api\/v1\/scheduler\/tasks(\/\d+)?$/,
          /\/api\/v1\/projects(\/|$)/,
          /\/api\/v1\/users(\/|$)/
        ]
        const inWhitelist = mutationWhitelist.some(r => r.test(url))

        // GET 请求默认不显示成功消息，除非有明确的错误或警告
        if (msg && !isNoisyQueryMsg) {
          if (isMutation || data.success === false) {
            const ok = data.success === undefined ? true : !!data.success
            const type = ok ? 'success' : 'warning'
            const shouldSkip = ok && !!skipSuccessToast
            if (!shouldSkip) {
              showNotification(type as any, msg)
            }
          }
        } else if ((data?.success === false) && !msg) {
          const detail = Array.isArray(data?.detail)
            ? data.detail.map((d: any) => d?.msg || d).join(', ')
            : (data?.detail || data?.error || data?.errors?.join?.(', ') || '请求存在警告')
          if (!skipSuccessToast) {
            showNotification('warning', String(detail))
          }
        } else if (isMutation && inWhitelist && !msg) {
          if (!skipSuccessToast) {
            showNotification('success', '操作成功')
          }
        }
      }
    } catch (err) {
      console.warn('Response interceptor error:', err)
    }
    return response
  },
  // ==================== 响应拦截器 - 错误处理 ====================
  (error) => {
    if (error.response) {
      const { status, data } = error.response
      const cfg = error.config || {}
      const method = (cfg.method || '').toUpperCase()
      const url = cfg.url || ''
      
      // 优先展示后端返回的 message/detail
      let msg: string | undefined = data?.message || data?.detail
      
      // 422 特殊处理：detail 可能为数组
      if (!msg && status === 422 && data?.detail && Array.isArray(data.detail)) {
        const errorMessages = data.detail.map((err: any) => err?.msg || '').filter(Boolean).join(', ')
        msg = errorMessages ? `参数错误: ${errorMessages}` : '参数验证失败'
      }

      // 按状态码回退
      if (!msg) {
        switch (status) {
          case 401:
            AuthHandler.handleAuthFailure()
            msg = '未认证或登录已过期'
            break
          case 403:
            msg = '权限不足'
            break
          case 404:
            msg = '请求的资源不存在'
            break
          case 429:
            msg = '请求过于频繁，请稍后再试'
            break
          case 500:
            msg = '服务器内部错误'
            break
          case 502:
            msg = '网关错误'
            break
          case 503:
            msg = '服务暂时不可用'
            break
          default:
            msg = '请求失败'
        }
      } else if (status === 401) {
        // 有后端消息也要处理登录态
        AuthHandler.handleAuthFailure()
      }
      
      showNotification('error', msg)
    } else if (error.request) {
      showNotification('error', '网络连接失败，请检查网络设置')
    } else {
      showNotification('error', '请求配置错误')
    }
    
    return Promise.reject(error)
  }
)

// ==================== Token 管理工具类 ====================
export class TokenManager {
  private static readonly TOKEN_KEY = STORAGE_KEYS.ACCESS_TOKEN
  private static readonly REFRESH_KEY = STORAGE_KEYS.REFRESH_TOKEN

  static setTokens(accessToken: string, refreshToken?: string) {
    localStorage.setItem(this.TOKEN_KEY, accessToken)
    if (refreshToken) {
      localStorage.setItem(this.REFRESH_KEY, refreshToken)
    }
  }

  static getAccessToken(): string | null {
    return localStorage.getItem(this.TOKEN_KEY)
  }

  static getRefreshToken(): string | null {
    return localStorage.getItem(this.REFRESH_KEY)
  }

  static clearTokens() {
    localStorage.removeItem(this.TOKEN_KEY)
    localStorage.removeItem(this.REFRESH_KEY)
    localStorage.removeItem(STORAGE_KEYS.USER_INFO)
  }

  static isTokenExpired(token: string): boolean {
    try {
      const payload = JSON.parse(atob(token.split('.')[1]))
      return payload.exp * 1000 < Date.now()
    } catch {
      return true
    }
  }

  static getTokenPayload(token: string): any {
    try {
      return JSON.parse(atob(token.split('.')[1]))
    } catch {
      return null
    }
  }
}

// ==================== 请求重试配置 ====================
export const retryConfig = {
  retries: 3,
  retryDelay: (retryCount: number) => {
    return Math.pow(2, retryCount) * 1000 // 指数退避
  },
  retryCondition: (error: any) => {
    return error.response?.status >= 500 || error.code === 'NETWORK_ERROR'
  }
}

// 添加请求重试功能
apiClient.interceptors.response.use(
  (response) => response,
  async (error) => {
    const config = error.config
    
    if (!config || !config.retry) {
      return Promise.reject(error)
    }
    
    config.retryCount = config.retryCount || 0
    
    if (config.retryCount >= retryConfig.retries) {
      return Promise.reject(error)
    }
    
    if (!retryConfig.retryCondition(error)) {
      return Promise.reject(error)
    }
    
    config.retryCount++
    
    const delay = retryConfig.retryDelay(config.retryCount)
    await new Promise(resolve => setTimeout(resolve, delay))
    
    return apiClient(config)
  }
)

// ==================== 创建带重试的请求方法 ====================
export const createRetryableRequest = (config: AxiosRequestConfig) => {
  return apiClient({
    ...config,
    retry: true
  } as AxiosRequestConfig & { retry: boolean })
}

export default apiClient
