import type { AuthRefreshPayload } from './api'
import {
  decodeAccessToken,
  getAccessToken,
  getSessionAccount,
  getSessionGeneration,
  setAccessToken,
  setSessionGeneration,
  setSessionHint,
} from './authToken'
import {
  publishAccessToken,
  requestPeerAccessToken,
  withCrossTabRefreshLock,
} from './authSessionChannel'
import { useAuthStore } from '@/stores/authStore'

const hasChanged = (candidate: string | null, previous: string | null): candidate is string => {
  return Boolean(candidate && candidate !== previous)
}

export class AuthAccountChangedError extends Error {
  constructor() {
    super('认证账号已切换，已取消旧账号请求')
    this.name = 'AuthAccountChangedError'
  }
}

export type StaleTokenResolution =
  | { kind: 'replace'; token: string }
  | { kind: 'account_changed' }
  | { kind: 'unavailable' }

const sharedPayload = (accessToken: string): AuthRefreshPayload => ({
  access_token: accessToken,
  token_type: 'bearer',
})

const tokenUsername = (token: string | null): string | undefined => {
  if (!token) return undefined
  const username = decodeAccessToken(token)?.username
  return typeof username === 'string' ? username : undefined
}

const tokenSessionJti = (token: string | null): string | undefined => {
  if (!token) return undefined
  const sessionJti = decodeAccessToken(token)?.session_jti
  return typeof sessionJti === 'string' ? sessionJti : undefined
}

const installToken = (token: string): void => {
  const sessionJti = tokenSessionJti(token)
  if (!sessionJti) throw new Error('access token 缺少 session_jti')
  const username = tokenUsername(token)
  if (!username) throw new Error('access token 缺少 username')
  setSessionGeneration(sessionJti, username)
  setAccessToken(token)
  const store = useAuthStore.getState()
  if (store.user && store.user.username !== username) store.clearUser()
  setSessionHint()
  publishAccessToken(token)
}

export const coordinateSessionRefresh = async (
  requestRefresh: () => Promise<AuthRefreshPayload>,
): Promise<AuthRefreshPayload> => {
  const previousToken = getAccessToken()
  const previousUsername = tokenUsername(previousToken)
  return withCrossTabRefreshLock(async () => {
    const currentToken = getAccessToken()
    const generation = getSessionGeneration()
    const sharedUsername = getSessionAccount()
    if (previousUsername && sharedUsername && sharedUsername !== previousUsername) {
      throw new AuthAccountChangedError()
    }
    if (hasChanged(currentToken, previousToken)
      && tokenSessionJti(currentToken) === generation) {
      if (previousUsername && tokenUsername(currentToken) !== previousUsername) {
        throw new AuthAccountChangedError()
      }
      return sharedPayload(currentToken)
    }
    const peerToken = generation
      ? await requestPeerAccessToken({ sessionJti: generation, username: previousUsername })
      : null
    if (hasChanged(peerToken, previousToken)) return sharedPayload(peerToken)
    const payload = await requestRefresh()
    if (previousUsername && tokenUsername(payload.access_token) !== previousUsername) {
      throw new AuthAccountChangedError()
    }
    installToken(payload.access_token)
    return payload
  })
}

export const replacementForStaleToken = async (
  staleToken: string,
): Promise<StaleTokenResolution> => {
  const staleUsername = tokenUsername(staleToken)
  const currentToken = getAccessToken()
  const generation = getSessionGeneration()
  const sharedUsername = getSessionAccount()
  if (staleUsername && sharedUsername && staleUsername !== sharedUsername) {
    return { kind: 'account_changed' }
  }
  if (hasChanged(currentToken, staleToken)
    && tokenSessionJti(currentToken) === generation) {
    if (staleUsername && tokenUsername(currentToken) !== staleUsername) {
      return { kind: 'account_changed' }
    }
    return { kind: 'replace', token: currentToken }
  }
  const peerToken = generation
    ? await requestPeerAccessToken({ sessionJti: generation, username: staleUsername })
    : null
  return hasChanged(peerToken, staleToken)
    ? { kind: 'replace', token: peerToken }
    : { kind: 'unavailable' }
}

export const synchronizeAccessToken = (token: string): void => installToken(token)
