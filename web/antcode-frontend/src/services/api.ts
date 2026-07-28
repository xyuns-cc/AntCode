import axios from 'axios'
import type { AxiosError, AxiosInstance, AxiosRequestConfig, AxiosResponse, InternalAxiosRequestConfig } from 'axios'
import showNotification from '@/utils/notification'
import { API_BASE_URL, STORAGE_KEYS } from '@/utils/constants'
import { AuthHandler } from '@/utils/authHandler'
import { isAbortError } from '@/utils/helpers'
import { presentApiError } from '@/utils/apiErrorPresentation'
import {
  broadcastAuthEvent,
  clearAccessToken,
  clearSessionHint,
  decodeAccessToken,
  getAccessToken,
  getSessionAccount,
  getSessionGeneration,
} from './authToken'
import {
  AuthAccountChangedError,
  coordinateSessionRefresh,
  replacementForStaleToken,
  synchronizeAccessToken,
} from './authRefreshCoordinator'
import {
  requestPeerAccessToken,
  withCrossTabRefreshLock,
} from './authSessionChannel'
import {
  markRequestAuthSession,
  rejectStaleAuthSessionResponse,
  StaleAuthSessionResponseError,
  type AuthSessionRequestConfig,
} from './authRequestSession'
import type { User } from '@/types'

const apiClient: AxiosInstance = axios.create({
  baseURL: API_BASE_URL,
  timeout: 30000,
  withCredentials: true,
  headers: {
    'Content-Type': 'application/json'
  }
})

// Independent axios instance for token refresh to avoid interceptor recursion
const refreshClient: AxiosInstance = axios.create({
  baseURL: API_BASE_URL,
  timeout: 15000,
  withCredentials: true,
  headers: {
    'Content-Type': 'application/json'
  }
})

// Token refresh state
let isRefreshing = false
let refreshPromise: Promise<AuthRefreshPayload> | null = null

const TOKEN_REFRESH_THRESHOLD_MS = 10 * 60 * 1000 // 10 minutes

async function ensureFreshToken(): Promise<string | null> {
  const token = TokenManager.getAccessToken()
  if (!token) return null

  const payload = TokenManager.getTokenPayload(token)
  const generation = getSessionGeneration()
  const sharedUsername = getSessionAccount()
  if (payload?.session_jti && !generation) {
    AuthHandler.handleAuthFailure(false)
    throw new Error('共享会话已结束，已取消当前请求')
  }
  const expiresAt = (payload?.exp || 0) * 1000
  const generationChanged = Boolean(
    generation && payload?.session_jti !== generation,
  )
  if (generationChanged && sharedUsername && payload?.username !== sharedUsername) {
    throw new AuthAccountChangedError()
  }
  if (!generationChanged && expiresAt - Date.now() > TOKEN_REFRESH_THRESHOLD_MS) return token

  try {
    const refreshed = isRefreshing && refreshPromise
      ? await refreshPromise
      : await refreshSessionToken()
    const refreshedPayload = TokenManager.getTokenPayload(refreshed.access_token)
    const accountBefore = payload?.user_id ?? payload?.username
    const accountAfter = refreshedPayload?.user_id ?? refreshedPayload?.username
    if (accountBefore !== undefined && accountAfter !== accountBefore) {
      throw new AuthAccountChangedError()
    }
    if (getSessionGeneration() !== refreshedPayload?.session_jti) {
      throw new AuthAccountChangedError()
    }
    return refreshed.access_token
  } catch (err) {
    if (err instanceof AuthAccountChangedError) throw err
    const status = (err as AxiosError)?.response?.status
    if (status === 401 || status === 403) {
      clearSessionHint()
      broadcastAuthEvent('logout')
      AuthHandler.handleAuthFailure()
      return null
    }
    if (getSessionGeneration() !== payload?.session_jti) {
      throw new Error('无法同步当前共享会话，已取消请求', { cause: err })
    }
    return token
  }
}

const requestRefreshPayload = async (): Promise<AuthRefreshPayload> => {
  const response = await refreshClient.post('/api/v1/auth/refresh', {})
  const payload = response.data?.data as AuthRefreshPayload | undefined
  if (!payload?.access_token) throw new Error('刷新响应缺少 access_token')
  return payload
}

export async function refreshSessionToken(): Promise<AuthRefreshPayload> {
  if (refreshPromise) return refreshPromise
  isRefreshing = true
  refreshPromise = coordinateSessionRefresh(requestRefreshPayload)
  try {
    return await refreshPromise
  } finally {
    isRefreshing = false
    refreshPromise = null
  }
}

type LoginResponseBody = { data?: { access_token?: string } }

export const requestSessionLogin = <T extends LoginResponseBody>(
  payload: unknown,
): Promise<AxiosResponse<T>> => {
  return withCrossTabRefreshLock(async () => {
    const response = await refreshClient.post<T>('/api/v1/auth/login', payload)
    const accessToken = response.data?.data?.access_token
    if (!accessToken) throw new Error('登录响应缺少 access_token')
    synchronizeAccessToken(accessToken)
    return response
  })
}

export const requestSessionLogout = (): Promise<AxiosResponse> => {
  return withCrossTabRefreshLock(async () => {
    const generation = getSessionGeneration()
    let accessToken = getAccessToken()
    const payload = accessToken ? decodeAccessToken(accessToken) : null
    if (generation && payload?.session_jti !== generation) {
      accessToken = await requestPeerAccessToken({ sessionJti: generation })
    }
    if (!accessToken && generation) {
      const refreshed = await requestRefreshPayload()
      synchronizeAccessToken(refreshed.access_token)
      accessToken = refreshed.access_token
    }
    if (!accessToken) throw new Error('当前标签页没有可用于登出的 access token')
    return refreshClient.post('/api/v1/auth/logout', {}, {
      headers: { Authorization: `Bearer ${accessToken}` },
    })
  })
}

apiClient.interceptors.request.use(async (config) => {
  const token = await ensureFreshToken()
  if (token && config.headers) {
    config.headers.Authorization = `Bearer ${token}`
  }
  markRequestAuthSession(config, token)
  return config
})

const requestAccessToken = (config: InternalAxiosRequestConfig | undefined): string | null => {
  const authorization = config?.headers?.Authorization
  if (typeof authorization !== 'string' || !authorization.startsWith('Bearer ')) return null
  return authorization.slice('Bearer '.length)
}

type AuthRetryOutcome =
  | { kind: 'retried'; response: AxiosResponse }
  | { kind: 'account_changed' }
  | { kind: 'unavailable' }

const retryWithReplacementToken = async (error: AxiosError): Promise<AuthRetryOutcome> => {
  const config = error.config as AuthSessionRequestConfig | undefined
  const staleToken = requestAccessToken(config)
  if (!config || !staleToken || config.authTokenRetried) return { kind: 'unavailable' }
  const resolution = await replacementForStaleToken(staleToken)
  if (resolution.kind !== 'replace') return resolution
  config.authTokenRetried = true
  config.headers.Authorization = `Bearer ${resolution.token}`
  return { kind: 'retried', response: await apiClient.request(config) }
}

apiClient.interceptors.response.use(
  rejectStaleAuthSessionResponse,
  async (error: AxiosError) => {
    if (isAbortError(error)) {
      return Promise.reject(error)
    }

    if (error.response?.status === 401) {
      const retry = await retryWithReplacementToken(error)
      if (retry.kind === 'retried') return retry.response
      if (retry.kind === 'account_changed') return Promise.reject(error)
      clearSessionHint()
      broadcastAuthEvent('logout')
      AuthHandler.handleAuthFailure()
    }

    const { title, description } = presentApiError(error)
    showNotification('error', title, description)
    return Promise.reject(error)
  }
)

const shouldRetry = (error: AxiosError) => {
  if (error instanceof StaleAuthSessionResponseError) return false
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
  static getTokenPayload(token: string): JwtTokenPayload | null {
    return decodeAccessToken(token) as JwtTokenPayload | null
  }

  static setTokens(accessToken: string) {
    synchronizeAccessToken(accessToken)
  }

  static getAccessToken(): string | null {
    return getAccessToken()
  }

  static clearTokens() {
    clearAccessToken()
    localStorage.removeItem(STORAGE_KEYS.ACCESS_TOKEN)
    localStorage.removeItem(STORAGE_KEYS.REFRESH_TOKEN)
    sessionStorage.removeItem(STORAGE_KEYS.REFRESH_TOKEN)
    localStorage.removeItem(STORAGE_KEYS.USER_INFO)
  }

  static isTokenExpired(token: string): boolean {
    const payload = this.getTokenPayload(token)
    if (!payload?.exp) return true
    return payload.exp * 1000 < Date.now()
  }
}

export type AuthRefreshPayload = {
  access_token: string
  token_type: string
  expires_in?: number
  user?: User
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
