/**
 * HTTP 客户端配置
 * 统一的 axios 实例，精简拦截器与重试逻辑
 */

import axios from 'axios'
import type { AxiosError, AxiosInstance, AxiosRequestConfig, AxiosResponse } from 'axios'
import showNotification from '@/utils/notification'
import { API_BASE_URL, STORAGE_KEYS } from '@/utils/constants'
import { AuthHandler } from '@/utils/authHandler'
import { isAbortError } from '@/utils/helpers'
import { presentApiError } from '@/utils/apiErrorPresentation'

const apiClient: AxiosInstance = axios.create({
  baseURL: API_BASE_URL,
  timeout: 30000,
  headers: {
    'Content-Type': 'application/json'
  }
})

// Independent axios instance for token refresh to avoid interceptor recursion
const refreshClient: AxiosInstance = axios.create({
  baseURL: API_BASE_URL,
  timeout: 15000,
  headers: {
    'Content-Type': 'application/json'
  }
})

// Token refresh state
let isRefreshing = false
let refreshPromise: Promise<string | null> | null = null

const TOKEN_REFRESH_THRESHOLD_MS = 10 * 60 * 1000 // 10 minutes

async function ensureFreshToken(): Promise<string | null> {
  const token = localStorage.getItem(STORAGE_KEYS.ACCESS_TOKEN)
  if (!token) return null

  // Check if token expires within threshold
  try {
    const payload = JSON.parse(atob(token.split('.')[1]))
    const expiresAt = (payload.exp || 0) * 1000
    const remaining = expiresAt - Date.now()

    if (remaining > TOKEN_REFRESH_THRESHOLD_MS) {
      return token // Still fresh enough
    }

    // Token is about to expire, refresh it
    const refreshToken = localStorage.getItem(STORAGE_KEYS.REFRESH_TOKEN)
    if (!refreshToken) return token // No refresh token, use current

    if (isRefreshing && refreshPromise) {
      return refreshPromise // Wait for ongoing refresh
    }

    isRefreshing = true
    refreshPromise = (async () => {
      try {
        const response = await refreshClient.post('/api/v1/auth/refresh', {
          refresh_token: refreshToken,
        })
        const data = response.data?.data
        if (data?.access_token) {
          localStorage.setItem(STORAGE_KEYS.ACCESS_TOKEN, data.access_token)
          if (data.refresh_token) {
            localStorage.setItem(STORAGE_KEYS.REFRESH_TOKEN, data.refresh_token)
          }
          if (data.user) {
            localStorage.setItem(STORAGE_KEYS.USER_INFO, JSON.stringify(data.user))
          }
          return data.access_token
        }
        return token
      } catch {
        return token // Refresh failed, use existing token
      } finally {
        isRefreshing = false
        refreshPromise = null
      }
    })()

    return refreshPromise
  } catch {
    return token
  }
}

apiClient.interceptors.request.use(async (config) => {
  const token = await ensureFreshToken()
  if (token && config.headers) {
    config.headers.Authorization = `Bearer ${token}`
  }
  return config
})

apiClient.interceptors.response.use(
  (response: AxiosResponse) => response,
  (error: AxiosError) => {
    if (isAbortError(error)) {
      return Promise.reject(error)
    }

    if (error.response?.status === 401) {
      AuthHandler.handleAuthFailure()
    }

    const { title, description } = presentApiError(error)
    showNotification('error', title, description)
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

  static getTokenPayload(token: string): JwtTokenPayload | null {
    try {
      return JSON.parse(atob(token.split('.')[1])) as JwtTokenPayload
    } catch {
      return null
    }
  }

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
    const payload = this.getTokenPayload(token)
    if (!payload?.exp) return true
    return payload.exp * 1000 < Date.now()
  }
}

export type JwtTokenPayload = {
  exp?: number
  iat?: number
  user_id?: number | string
  username?: string
  permissions?: string[]
  [key: string]: unknown
}

export default apiClient
