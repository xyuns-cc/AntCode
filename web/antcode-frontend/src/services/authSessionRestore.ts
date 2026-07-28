import { isAxiosError } from 'axios'
import { useAuthStore } from '@/stores/authStore'
import { AuthHandler } from '@/utils/authHandler'
import Logger from '@/utils/logger'
import { authService } from './auth'
import {
  requestPeerAccessToken,
  subscribeAccessTokenUpdates,
} from './authSessionChannel'
import {
  broadcastAuthEvent,
  clearAccessToken,
  clearSessionGeneration,
  clearSessionHint,
  decodeAccessToken,
  getAccessToken,
  getSessionGeneration,
  hasSessionHint,
  setSessionHint,
  subscribeAuthEvents,
} from './authToken'
import type { AuthBroadcastMessage } from './authToken'

let sessionRestorePromise: Promise<void> | null = null
let sessionRestoreInFlight = false
let sessionRestoreSettled = false
let requestedRestoreVersion = 0
let completedRestoreVersion = 0

const errorMessage = (error: unknown): string => {
  if (isAxiosError(error)) {
    const detail = (error.response?.data as { detail?: string } | undefined)?.detail
    return detail || error.message || '检查登录状态失败'
  }
  return error instanceof Error ? error.message : '检查登录状态失败'
}

type LiveTokenIdentity = { username: string | null }

const liveTokenIdentity = (): LiveTokenIdentity | null => {
  const token = getAccessToken()
  if (!token) return null
  const payload = decodeAccessToken(token)
  const expiresAt = typeof payload?.exp === 'number' ? payload.exp * 1000 : 0
  if (expiresAt <= Date.now()) return null
  return {
    username: typeof payload?.username === 'string' ? payload.username : null,
  }
}

const performSessionRestore = async (): Promise<void> => {
  const startEpoch = useAuthStore.getState().authEpoch
  let restoreGeneration = getSessionGeneration()
  if (!liveTokenIdentity() && !hasSessionHint()) {
    const store = useAuthStore.getState()
    if (store.isAuthenticated && store.authEpoch === startEpoch) store.clearUser()
    return
  }

  try {
    const user = await authService.restoreSession()
    restoreGeneration = getSessionGeneration()
    const permissions = await authService.getUserPermissions()
    const store = useAuthStore.getState()
    if (store.authEpoch !== startEpoch) return
    if (getSessionGeneration() !== restoreGeneration) return
    store.setPermissions(permissions)
    store.setUser(user)
    setSessionHint()
  } catch (error: unknown) {
    Logger.warn('检查登录状态失败:', error)
    const store = useAuthStore.getState()
    if (store.authEpoch !== startEpoch) return
    if (getSessionGeneration() !== restoreGeneration) return
    clearAccessToken()
    const status = isAxiosError(error) ? error.response?.status : undefined
    if (status === 401 || status === 403) {
      clearSessionGeneration()
      clearSessionHint()
      broadcastAuthEvent('logout')
    }
    store.clearUser()
    store.setError(errorMessage(error))
  }
}

const drainSessionRestores = async (): Promise<void> => {
  sessionRestoreInFlight = true
  sessionRestoreSettled = false
  try {
    while (completedRestoreVersion < requestedRestoreVersion) {
      const targetVersion = requestedRestoreVersion
      await performSessionRestore()
      completedRestoreVersion = targetVersion
    }
  } finally {
    sessionRestoreInFlight = false
    sessionRestoreSettled = true
  }
}

const startSessionRestore = (): Promise<void> => {
  sessionRestorePromise = drainSessionRestores()
  return sessionRestorePromise
}

export const ensureSessionRestored = (options?: { force?: boolean }): Promise<void> => {
  if (!sessionRestorePromise) {
    requestedRestoreVersion += 1
    return startSessionRestore()
  }
  if (options?.force) {
    requestedRestoreVersion += 1
    if (!sessionRestoreInFlight) return startSessionRestore()
  }
  return sessionRestorePromise
}

export const isSessionRestoreSettled = (): boolean => sessionRestoreSettled

export const resetSessionRestore = (): void => {
  sessionRestorePromise = null
  sessionRestoreInFlight = false
  sessionRestoreSettled = false
  requestedRestoreVersion = 0
  completedRestoreVersion = 0
}

export const handleAuthBroadcast = async (event: AuthBroadcastMessage): Promise<void> => {
  if (event.type === 'logout') {
    AuthHandler.handleAuthFailure(false)
    return
  }

  const store = useAuthStore.getState()
  if (event.username && store.user?.username !== event.username) store.clearUser()
  if (event.username && liveTokenIdentity()?.username !== event.username) {
    clearAccessToken()
    const sessionJti = getSessionGeneration()
    if (sessionJti) await requestPeerAccessToken({ sessionJti, username: event.username })
  }
  await ensureSessionRestored({ force: true })
}

export const handlePeerAccessTokenUpdate = async (token: string): Promise<void> => {
  const username = decodeAccessToken(token)?.username
  if (typeof username !== 'string') return
  const store = useAuthStore.getState()
  if (store.user?.username !== username) store.clearUser()
  await ensureSessionRestored({ force: true })
}

if (typeof window !== 'undefined') {
  subscribeAuthEvents((event) => {
    void handleAuthBroadcast(event)
  })
  subscribeAccessTokenUpdates((token) => {
    void handlePeerAccessTokenUpdate(token)
  })
}
