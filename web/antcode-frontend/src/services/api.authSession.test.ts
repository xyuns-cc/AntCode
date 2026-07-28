import { beforeEach, describe, expect, it, vi } from 'vitest'

const mocks = vi.hoisted(() => ({
  apiRequest: vi.fn(),
  authFailure: vi.fn(),
  broadcastAuthEvent: vi.fn(),
  clearSessionHint: vi.fn(),
  coordinateSessionRefresh: vi.fn(),
  create: vi.fn(),
  decodeAccessToken: vi.fn(),
  getAccessToken: vi.fn(),
  getSessionAccount: vi.fn(),
  getSessionGeneration: vi.fn(),
  replacementForStaleToken: vi.fn(),
  requestPeerAccessToken: vi.fn(),
  requestFulfilled: undefined as undefined | ((config: unknown) => Promise<unknown>),
  responseFulfilled: undefined as undefined | ((response: unknown) => unknown),
  responseRejected: undefined as undefined | ((error: unknown) => Promise<unknown>),
  synchronizeAccessToken: vi.fn(),
  withCrossTabRefreshLock: vi.fn(async <T>(action: () => Promise<T>) => action()),
}))

const requestInterceptors = {
  use: vi.fn((fulfilled: (config: unknown) => Promise<unknown>) => {
    mocks.requestFulfilled = fulfilled
  }),
}
const responseInterceptors = {
  use: vi.fn((fulfilled: (response: unknown) => unknown, rejected: (error: unknown) => Promise<unknown>) => {
    mocks.responseFulfilled = fulfilled
    mocks.responseRejected = rejected
  }),
}
const apiClient = {
  interceptors: { request: requestInterceptors, response: responseInterceptors },
  request: mocks.apiRequest,
}
const refreshClient = { post: vi.fn() }

vi.mock('axios', () => ({
  default: { create: mocks.create },
}))
vi.mock('./authRefreshCoordinator', () => ({
  AuthAccountChangedError: class AuthAccountChangedError extends Error {
    constructor() {
      super('认证账号已切换，已取消旧账号请求')
    }
  },
  coordinateSessionRefresh: mocks.coordinateSessionRefresh,
  replacementForStaleToken: mocks.replacementForStaleToken,
  synchronizeAccessToken: mocks.synchronizeAccessToken,
}))
vi.mock('./authSessionChannel', () => ({
  requestPeerAccessToken: mocks.requestPeerAccessToken,
  withCrossTabRefreshLock: mocks.withCrossTabRefreshLock,
}))
vi.mock('./authToken', () => ({
  broadcastAuthEvent: mocks.broadcastAuthEvent,
  clearAccessToken: vi.fn(),
  clearSessionHint: mocks.clearSessionHint,
  decodeAccessToken: mocks.decodeAccessToken,
  getAccessToken: mocks.getAccessToken,
  getSessionAccount: mocks.getSessionAccount,
  getSessionGeneration: mocks.getSessionGeneration,
}))
vi.mock('@/utils/authHandler', () => ({
  AuthHandler: { handleAuthFailure: mocks.authFailure },
}))
vi.mock('@/utils/notification', () => ({ default: vi.fn() }))

let apiModule: typeof import('./api')

describe('API stale-session retry', () => {
  beforeEach(async () => {
    vi.resetModules()
    mocks.create
      .mockReturnValueOnce(apiClient)
      .mockReturnValueOnce(refreshClient)
    apiModule = await import('./api')
  })

  it('cancels a request when an in-flight refresh resolves for another account', async () => {
    mocks.getAccessToken.mockReturnValue('old-account-token')
    mocks.decodeAccessToken.mockImplementation((token: string) => ({
      exp: Math.floor(Date.now() / 1000) + 60,
      user_id: token === 'new-account-token' ? 2 : 1,
    }))
    mocks.coordinateSessionRefresh.mockResolvedValue({
      access_token: 'new-account-token',
      token_type: 'bearer',
    })
    const config = { headers: {} }

    await expect(mocks.requestFulfilled?.(config)).rejects.toThrow('认证账号已切换')
    expect(config.headers).not.toHaveProperty('Authorization')
  })

  it('fences a still-fresh token when the shared session generation changed', async () => {
    mocks.getAccessToken.mockReturnValue('old-token')
    mocks.getSessionGeneration.mockReturnValue('session-new')
    mocks.decodeAccessToken.mockImplementation((token: string) => ({
      exp: Math.floor(Date.now() / 1000) + 3600,
      user_id: 1,
      session_jti: token === 'new-token' ? 'session-new' : 'session-old',
    }))
    mocks.coordinateSessionRefresh.mockResolvedValue({
      access_token: 'new-token',
      token_type: 'bearer',
    })
    const config = { headers: {} as Record<string, string> }

    await expect(mocks.requestFulfilled?.(config)).resolves.toBe(config)
    expect(config.headers.Authorization).toBe('Bearer new-token')
  })

  it('cancels a fresh old-account request before touching the new refresh cookie', async () => {
    mocks.getAccessToken.mockReturnValue('alice-token')
    mocks.getSessionGeneration.mockReturnValue('bob-session')
    mocks.getSessionAccount.mockReturnValue('bob')
    mocks.decodeAccessToken.mockReturnValue({
      exp: Math.floor(Date.now() / 1000) + 3600,
      username: 'alice',
      session_jti: 'alice-session',
    })
    const config = { headers: {} }

    await expect(mocks.requestFulfilled?.(config)).rejects.toThrow('认证账号已切换')
    expect(mocks.coordinateSessionRefresh).not.toHaveBeenCalled()
    expect(config.headers).not.toHaveProperty('Authorization')
  })

  it('installs a login token while holding the shared session lock', async () => {
    refreshClient.post.mockResolvedValue({
      data: { data: { access_token: 'login-token' } },
    })

    await apiModule.requestSessionLogin({ username: 'alice' })

    expect(mocks.withCrossTabRefreshLock).toHaveBeenCalledOnce()
    expect(mocks.synchronizeAccessToken).toHaveBeenCalledWith('login-token')
  })

  it('logs out with the token matching the current shared generation', async () => {
    mocks.getAccessToken.mockReturnValue('alice-token')
    mocks.getSessionGeneration.mockReturnValue('bob-session')
    mocks.decodeAccessToken.mockReturnValue({
      username: 'alice',
      session_jti: 'alice-session',
    })
    mocks.requestPeerAccessToken.mockResolvedValue('bob-token')
    refreshClient.post.mockResolvedValue({ data: { data: null } })

    await apiModule.requestSessionLogout()

    expect(mocks.requestPeerAccessToken).toHaveBeenCalledWith({
      sessionJti: 'bob-session',
    })
    expect(refreshClient.post).toHaveBeenCalledWith('/api/v1/auth/logout', {}, {
      headers: { Authorization: 'Bearer bob-token' },
    })
  })

  it('retries once with the token rotated by another tab', async () => {
    const response = { data: { ok: true } }
    mocks.replacementForStaleToken.mockResolvedValue({ kind: 'replace', token: 'new-token' })
    mocks.apiRequest.mockResolvedValue(response)
    const config = { headers: { Authorization: 'Bearer old-token' } }
    const error = { response: { status: 401 }, config }

    await expect(mocks.responseRejected?.(error)).resolves.toBe(response)
    expect(config).toMatchObject({
      authTokenRetried: true,
      headers: { Authorization: 'Bearer new-token' },
    })
    expect(mocks.authFailure).not.toHaveBeenCalled()
  })

  it('logs out only when no tab owns a replacement token', async () => {
    mocks.replacementForStaleToken.mockResolvedValue({ kind: 'unavailable' })
    const error = {
      message: 'unauthorized',
      response: { status: 401 },
      config: { headers: { Authorization: 'Bearer old-token' } },
    }

    await expect(mocks.responseRejected?.(error)).rejects.toBe(error)
    expect(mocks.clearSessionHint).toHaveBeenCalledOnce()
    expect(mocks.broadcastAuthEvent).toHaveBeenCalledWith('logout')
    expect(mocks.authFailure).toHaveBeenCalledOnce()
  })

  it('cancels an old-account request without logging the new account out', async () => {
    mocks.replacementForStaleToken.mockResolvedValue({ kind: 'account_changed' })
    const error = {
      message: 'old account unauthorized',
      response: { status: 401 },
      config: { headers: { Authorization: 'Bearer old-account-token' } },
    }

    await expect(mocks.responseRejected?.(error)).rejects.toBe(error)
    expect(mocks.apiRequest).not.toHaveBeenCalled()
    expect(mocks.clearSessionHint).not.toHaveBeenCalled()
    expect(mocks.broadcastAuthEvent).not.toHaveBeenCalled()
    expect(mocks.authFailure).not.toHaveBeenCalled()
  })

  it('rejects a successful response after the active account changes', async () => {
    mocks.getAccessToken.mockReturnValue('alice-token')
    mocks.getSessionGeneration.mockReturnValue('alice-session')
    mocks.getSessionAccount.mockReturnValue('alice')
    mocks.decodeAccessToken.mockReturnValue({
      exp: Math.floor(Date.now() / 1000) + 3600,
      username: 'alice',
      session_jti: 'alice-session',
    })
    const config = { headers: {} as Record<string, string> }
    await mocks.requestFulfilled?.(config)

    mocks.getSessionGeneration.mockReturnValue('bob-session')
    mocks.getSessionAccount.mockReturnValue('bob')
    const response = { config, data: { owner: 'alice' } }

    await expect(Promise.resolve().then(() => mocks.responseFulfilled?.(response)))
      .rejects.toThrow('认证会话已切换，已丢弃旧会话响应')
  })

  it('accepts a successful response from the current authenticated session', async () => {
    mocks.getAccessToken.mockReturnValue('alice-token')
    mocks.getSessionGeneration.mockReturnValue('alice-session')
    mocks.getSessionAccount.mockReturnValue('alice')
    mocks.decodeAccessToken.mockReturnValue({
      exp: Math.floor(Date.now() / 1000) + 3600,
      username: 'alice',
      session_jti: 'alice-session',
    })
    const config = { headers: {} as Record<string, string> }
    await mocks.requestFulfilled?.(config)
    const response = { config, data: { owner: 'alice' } }

    expect(mocks.responseFulfilled?.(response)).toBe(response)
  })

  it('does not fence public responses without a managed access token', async () => {
    mocks.getAccessToken.mockReturnValue(null)
    const config = { headers: {} }
    await mocks.requestFulfilled?.(config)
    mocks.getSessionGeneration.mockReturnValue('new-authenticated-session')
    mocks.getSessionAccount.mockReturnValue('alice')
    const response = { config, data: { public: true } }

    expect(mocks.responseFulfilled?.(response)).toBe(response)
  })
})
