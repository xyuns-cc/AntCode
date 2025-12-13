/**
 * HTTP 客户端配置
 * 统一的 axios 实例，精简拦截器与重试逻辑
 */

import axios from 'axios'
import type { AxiosError, AxiosInstance, AxiosRequestConfig, AxiosResponse } from 'axios'
import showNotification from '@/utils/notification'
import { API_BASE_URL, STORAGE_KEYS } from '@/utils/constants'
import { AuthHandler } from '@/utils/authHandler'

const apiClient: AxiosInstance = axios.create({
  baseURL: API_BASE_URL,
  timeout: 30000,
  headers: {
    'Content-Type': 'application/json'
  }
})

apiClient.interceptors.request.use((config) => {
  const token = localStorage.getItem(STORAGE_KEYS.ACCESS_TOKEN)
  if (token && config.headers) {
    config.headers.Authorization = `Bearer ${token}`
  }
  return config
})

type ErrorData = {
  message?: string
  detail?: string | Array<{ msg?: string }>
}

const buildErrorMessage = (error: AxiosError<ErrorData | unknown>) => {
  const status = error.response?.status
  const data = error.response?.data

  if (data && typeof data === 'object' && 'message' in data && (data as ErrorData).message) {
    return String((data as ErrorData).message)
  }
  if (Array.isArray((data as ErrorData | undefined)?.detail)) {
    const detailItems = (data as ErrorData).detail as Array<{ msg?: string } | string>
    const detail = detailItems
      .map((d) => (typeof d === 'string' ? d : d?.msg || ''))
      .filter(Boolean)
      .join(', ')
    if (detail) return `参数错误: ${detail}`
  }
  if (data && typeof data === 'object' && 'detail' in data && (data as ErrorData).detail) {
    return String((data as ErrorData).detail)
  }

  switch (status) {
    case 400: return '请求参数错误'
    case 401: return '未认证或登录已过期'
    case 403: return '权限不足'
    case 404: return '请求的资源不存在'
    case 429: return '请求过于频繁，请稍后再试'
    case 500: return '服务器内部错误'
    case 502: return '网关错误'
    case 503: return '服务暂时不可用'
    default: return error.message || '请求失败'
  }
}

apiClient.interceptors.response.use(
  (response: AxiosResponse) => response,
  (error: AxiosError) => {
    if (error.response?.status === 401) {
      AuthHandler.handleAuthFailure()
    }
    const message = buildErrorMessage(error)
    showNotification('error', message)
    return Promise.reject(error)
  }
)

const shouldRetry = (error: AxiosError) => {
  const status = error.response?.status
  if (!status) return true
  if (status === 401 || status === 403) return false
  return status >= 500
}

const wait = (ms: number) => new Promise(resolve => setTimeout(resolve, ms))

export const createRetryableRequest = async (config: AxiosRequestConfig, retries = 3) => {
  let attempt = 0
  let lastError: unknown = null

  while (attempt <= retries) {
    try {
      return await apiClient(config)
    } catch (error: unknown) {
      lastError = error
      const axiosError = error as AxiosError
      if (!shouldRetry(axiosError) || attempt === retries) break
      attempt += 1
      const delay = Math.pow(2, attempt) * 100
      await wait(delay)
    }
  }

  throw lastError
}

export const unwrapResponse = <T>(response: AxiosResponse<unknown>): T => {
  const data = response?.data
  if (data && typeof data === 'object' && 'data' in data) {
    return (data as { data: T }).data as T
  }
  return data as T
}

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

  static getTokenPayload(token: string): unknown {
    try {
      return JSON.parse(atob(token.split('.')[1]))
    } catch {
      return null
    }
  }
}

export default apiClient
