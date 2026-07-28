import type {
  AxiosResponse,
  InternalAxiosRequestConfig,
} from 'axios'
import {
  decodeAccessToken,
  getSessionAccount,
  getSessionGeneration,
} from './authToken'

type AuthSessionSnapshot = Readonly<{
  generation: string
  username?: string
}>

export type AuthSessionRequestConfig = InternalAxiosRequestConfig & {
  authSessionSnapshot?: AuthSessionSnapshot
  authTokenRetried?: boolean
}

export class StaleAuthSessionResponseError extends Error {
  constructor() {
    super('认证会话已切换，已丢弃旧会话响应')
    this.name = 'StaleAuthSessionResponseError'
  }
}

const sessionSnapshot = (token: string): AuthSessionSnapshot | undefined => {
  const payload = decodeAccessToken(token)
  const generation = payload?.session_jti
  if (typeof generation !== 'string') return undefined
  const username = payload?.username
  return {
    generation,
    username: typeof username === 'string' ? username : undefined,
  }
}

export const markRequestAuthSession = (
  config: InternalAxiosRequestConfig,
  token: string | null,
): void => {
  const requestConfig = config as AuthSessionRequestConfig
  requestConfig.authSessionSnapshot = token
    ? sessionSnapshot(token)
    : undefined
}

export const rejectStaleAuthSessionResponse = <T>(
  response: AxiosResponse<T>,
): AxiosResponse<T> => {
  const snapshot = (response.config as AuthSessionRequestConfig).authSessionSnapshot
  if (!snapshot) return response
  const generationChanged = getSessionGeneration() !== snapshot.generation
  const accountChanged = Boolean(
    snapshot.username && getSessionAccount() !== snapshot.username,
  )
  if (generationChanged || accountChanged) {
    throw new StaleAuthSessionResponseError()
  }
  return response
}
