import {
  decodeAccessToken,
  getAccessToken,
  getSessionGeneration,
  setAccessToken,
  setSessionHint,
} from './authToken'
import { withCrossTabLock } from './authCrossTabLock'

const AUTH_SESSION_CHANNEL = 'antcode-auth-session'
const AUTH_REFRESH_LOCK = 'antcode-auth-refresh'
const PEER_RESPONSE_WAIT_MS = 100
const REQUEST_ID_WORDS = 4

type SessionMessage =
  | { type: 'token_request'; requestId: string; sessionJti: string; username?: string }
  | { type: 'token_response'; requestId: string; token: string }
  | { type: 'token_update'; token: string; sessionJti: string }

type PendingRequest = {
  finish: (token: string | null) => void
  sessionJti: string
  username?: string
}

const pendingRequests = new Map<string, PendingRequest>()
const tokenUpdateListeners = new Set<(token: string) => void>()
let channel: BroadcastChannel | null = null

const isLiveToken = (token: string): boolean => {
  const payload = decodeAccessToken(token)
  return (
    payload?.token_type === 'access' &&
    typeof payload.username === 'string' &&
    typeof payload.session_jti === 'string' &&
    typeof payload.exp === 'number' &&
    payload.exp * 1000 > Date.now()
  )
}

const tokenSessionJti = (token: string): string | null => {
  const sessionJti = decodeAccessToken(token)?.session_jti
  return typeof sessionJti === 'string' ? sessionJti : null
}

const isSessionMessage = (value: unknown): value is SessionMessage => {
  if (!value || typeof value !== 'object') return false
  const message = value as {
    type?: unknown
    requestId?: unknown
    sessionJti?: unknown
    token?: unknown
    username?: unknown
  }
  if (message.type === 'token_request') {
    return (
      typeof message.requestId === 'string' &&
      typeof message.sessionJti === 'string' &&
      (message.username === undefined || typeof message.username === 'string')
    )
  }
  if (message.type === 'token_update') {
    return typeof message.token === 'string' && typeof message.sessionJti === 'string'
  }
  return (
    message.type === 'token_response' &&
    typeof message.requestId === 'string' &&
    typeof message.token === 'string'
  )
}

const tokenMatchesRequest = (token: string, pending: PendingRequest): boolean => {
  if (tokenSessionJti(token) !== pending.sessionJti) return false
  const { username } = pending
  if (!username) return true
  const payload = decodeAccessToken(token)
  return payload?.username === username
}

const acceptToken = (token: string, notify: boolean): boolean => {
  if (!isLiveToken(token)) return false
  setAccessToken(token)
  setSessionHint()
  if (notify) {
    for (const listener of tokenUpdateListeners) listener(token)
  }
  return true
}

const handleSessionMessage = (message: SessionMessage): void => {
  if (message.type === 'token_request') {
    const token = getAccessToken()
    const request = { finish: () => undefined, ...message }
    if (token && isLiveToken(token) && tokenMatchesRequest(token, request)) {
      getChannel()?.postMessage({ type: 'token_response', requestId: message.requestId, token })
    }
    return
  }
  if (message.type === 'token_update') {
    if (
      message.sessionJti === getSessionGeneration() &&
      tokenSessionJti(message.token) === message.sessionJti
    ) {
      acceptToken(message.token, true)
    }
    return
  }
  const pending = pendingRequests.get(message.requestId)
  if (
    pending &&
    pending.sessionJti === getSessionGeneration() &&
    tokenMatchesRequest(message.token, pending) &&
    acceptToken(message.token, true)
  ) {
    pending.finish(message.token)
  }
}

const getChannel = (): BroadcastChannel | null => {
  if (typeof BroadcastChannel === 'undefined') return null
  if (channel) return channel
  channel = new BroadcastChannel(AUTH_SESSION_CHANNEL)
  channel.addEventListener('message', (event) => {
    if (isSessionMessage(event.data)) handleSessionMessage(event.data)
  })
  ;(channel as unknown as { unref?: () => void }).unref?.()
  return channel
}

const createRequestId = (): string => {
  const values = new Uint32Array(REQUEST_ID_WORDS)
  crypto.getRandomValues(values)
  return Array.from(values, (value) => value.toString(16).padStart(8, '0')).join('')
}

export const publishAccessToken = (token: string): void => {
  if (!isLiveToken(token)) return
  const sessionJti = tokenSessionJti(token)
  if (!sessionJti || sessionJti !== getSessionGeneration()) return
  getChannel()?.postMessage({ type: 'token_update', token, sessionJti } satisfies SessionMessage)
}

export type PeerTokenRequest = { sessionJti: string; username?: string }

export const requestPeerAccessToken = (request: PeerTokenRequest): Promise<string | null> => {
  const activeChannel = getChannel()
  if (!activeChannel) return Promise.resolve(null)
  const requestId = createRequestId()
  return new Promise((resolve) => {
    const finish = (token: string | null) => {
      pendingRequests.delete(requestId)
      resolve(token)
    }
    pendingRequests.set(requestId, { finish, ...request })
    activeChannel.postMessage({
      type: 'token_request',
      requestId,
      ...request,
    } satisfies SessionMessage)
    setTimeout(() => finish(null), PEER_RESPONSE_WAIT_MS)
  })
}

export const withCrossTabRefreshLock = async <T>(action: () => Promise<T>): Promise<T> => {
  if (typeof BroadcastChannel === 'undefined') {
    throw new Error('当前浏览器不支持 BroadcastChannel，无法安全同步多标签会话')
  }
  return withCrossTabLock(AUTH_REFRESH_LOCK, action)
}

export const subscribeAccessTokenUpdates = (listener: (token: string) => void): (() => void) => {
  tokenUpdateListeners.add(listener)
  return () => tokenUpdateListeners.delete(listener)
}

export const resetAuthSessionChannelForTests = (): void => {
  channel?.close()
  channel = null
  for (const pending of pendingRequests.values()) pending.finish(null)
  pendingRequests.clear()
  tokenUpdateListeners.clear()
}
