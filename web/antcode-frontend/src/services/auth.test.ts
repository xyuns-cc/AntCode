import { beforeEach, describe, expect, it, vi } from 'vitest'
import type { User } from '@/types'

const mocks = vi.hoisted(() => ({
  authFailure: vi.fn(),
  broadcastAuthEvent: vi.fn(),
  clearAuthData: vi.fn(),
  clearSessionHint: vi.fn(),
  get: vi.fn(),
  post: vi.fn(),
  put: vi.fn(),
  refreshSessionToken: vi.fn(),
  getAccessToken: vi.fn(),
  getTokenPayload: vi.fn(),
  getSessionGeneration: vi.fn(),
  isTokenExpired: vi.fn(),
  encryptLoginPassword: vi.fn(),
  requestSessionLogin: vi.fn(),
  requestSessionLogout: vi.fn(),
  setTokens: vi.fn(),
  setSessionHint: vi.fn(),
  clearTokens: vi.fn(),
}))

vi.mock('./authToken', () => ({
  broadcastAuthEvent: mocks.broadcastAuthEvent,
  clearSessionHint: mocks.clearSessionHint,
  getSessionGeneration: mocks.getSessionGeneration,
  setSessionHint: mocks.setSessionHint,
}))

vi.mock('@/utils/authHandler', () => ({
  AuthHandler: {
    clearAuthData: mocks.clearAuthData,
    handleAuthFailure: mocks.authFailure,
  },
}))

vi.mock('./api', () => ({
  default: {
    get: mocks.get,
    post: mocks.post,
    put: mocks.put,
  },
  refreshSessionToken: mocks.refreshSessionToken,
  requestSessionLogin: mocks.requestSessionLogin,
  requestSessionLogout: mocks.requestSessionLogout,
  TokenManager: {
    getAccessToken: mocks.getAccessToken,
    getTokenPayload: mocks.getTokenPayload,
    isTokenExpired: mocks.isTokenExpired,
    setTokens: mocks.setTokens,
    clearTokens: mocks.clearTokens,
  },
}))

vi.mock('@/utils/loginEncryption', () => ({
  encryptLoginPassword: mocks.encryptLoginPassword,
}))

import { authService } from './auth'
import { useAuthStore } from '@/stores/authStore'

const user: User = {
  id: 'user-1',
  username: 'alice',
  is_active: true,
  is_admin: false,
  role: 'user',
  created_at: '2026-07-13T00:00:00Z',
  updated_at: '2026-07-13T00:00:00Z',
}

const adminUser: User = {
  ...user,
  is_admin: true,
  role: 'admin',
}

describe('authService.login', () => {
  beforeEach(() => {
    mocks.encryptLoginPassword.mockResolvedValue({
      encryptedPassword: 'encrypted',
      algorithm: 'RSA-OAEP-256',
      keyId: 'key-1',
    })
  })

  it('does not turn an invalid-password 401 into a global session logout', async () => {
    const error = { isAxiosError: true, response: { status: 401 } }
    mocks.requestSessionLogin.mockRejectedValue(error)

    await expect(authService.login({ username: 'alice', password: 'wrong' })).rejects.toBe(error)

    expect(mocks.broadcastAuthEvent).not.toHaveBeenCalled()
    expect(mocks.authFailure).not.toHaveBeenCalled()
  })
})

describe('authService.restoreSession', () => {
  beforeEach(() => {
    mocks.getAccessToken.mockReturnValue(null)
    mocks.isTokenExpired.mockReturnValue(false)
  })

  it('uses the HttpOnly refresh-cookie flow when memory has no access token', async () => {
    mocks.refreshSessionToken.mockResolvedValue({
      access_token: 'new-access-token',
      token_type: 'bearer',
      user,
    })

    await expect(authService.restoreSession()).resolves.toEqual(user)
    expect(mocks.refreshSessionToken).toHaveBeenCalledOnce()
    expect(mocks.get).not.toHaveBeenCalled()
  })

  it('loads the authoritative current user when the memory token is valid', async () => {
    mocks.getAccessToken.mockReturnValue('valid-access-token')
    mocks.get.mockResolvedValue({ data: { data: user } })

    await expect(authService.restoreSession()).resolves.toEqual(user)
    expect(mocks.get).toHaveBeenCalledWith('/api/v1/auth/me')
    expect(mocks.refreshSessionToken).not.toHaveBeenCalled()
  })

  it('surfaces refresh failures instead of fabricating an authenticated session', async () => {
    const error = new Error('refresh cookie expired')
    mocks.refreshSessionToken.mockRejectedValue(error)

    await expect(authService.restoreSession()).rejects.toBe(error)
    expect(mocks.setTokens).not.toHaveBeenCalled()
  })
})

describe('authService.getUserPermissions', () => {
  it('returns the exact permission list from the API', async () => {
    mocks.get.mockResolvedValue({ data: { data: { permissions: ['user:read'] } } })

    await expect(authService.getUserPermissions()).resolves.toEqual(['user:read'])
    expect(mocks.get).toHaveBeenCalledWith('/api/v1/auth/permissions')
  })

  it('rejects malformed permission responses', async () => {
    mocks.get.mockResolvedValue({ data: { data: {} } })

    await expect(authService.getUserPermissions()).rejects.toThrow('服务端未返回用户权限')
  })
})

describe('authService.changePassword', () => {
  beforeEach(() => {
    useAuthStore.setState({ user, isAuthenticated: true })
    mocks.put.mockResolvedValue({ data: { data: null } })
  })

  it('explicitly terminates the current tab after the backend revokes all sessions', async () => {
    await authService.changePassword('old-password', 'new-password')

    expect(mocks.put).toHaveBeenCalledWith('/api/v1/users/user-1/password', {
      old_password: 'old-password',
      new_password: 'new-password',
    })
    expect(mocks.clearSessionHint).toHaveBeenCalledOnce()
    expect(mocks.authFailure).toHaveBeenCalledWith(false)
    expect(mocks.broadcastAuthEvent).toHaveBeenCalledWith('logout')
    expect(mocks.refreshSessionToken).not.toHaveBeenCalled()
  })
})

describe('authService.autoRefreshToken', () => {
  beforeEach(() => {
    mocks.getAccessToken.mockReturnValue('expiring-access-token')
    mocks.getTokenPayload.mockReturnValue({ exp: Math.floor(Date.now() / 1000) + 60 })
    mocks.getSessionGeneration.mockReturnValue('generation-current')
  })

  it('logs every tab out when the refresh session is explicitly rejected', async () => {
    mocks.refreshSessionToken.mockRejectedValue({
      isAxiosError: true,
      response: { status: 401 },
    })

    await authService.autoRefreshToken()

    expect(mocks.clearSessionHint).toHaveBeenCalledOnce()
    expect(mocks.authFailure).toHaveBeenCalledWith(false)
    expect(mocks.broadcastAuthEvent).toHaveBeenCalledWith('logout')
    expect(mocks.clearTokens).not.toHaveBeenCalled()
  })

  it('keeps the current session intact after a transient refresh failure', async () => {
    mocks.refreshSessionToken.mockRejectedValue(new Error('network unavailable'))

    await authService.autoRefreshToken()

    expect(mocks.clearSessionHint).not.toHaveBeenCalled()
    expect(mocks.authFailure).not.toHaveBeenCalled()
    expect(mocks.broadcastAuthEvent).not.toHaveBeenCalled()
    expect(mocks.clearTokens).not.toHaveBeenCalled()
  })

  it('reloads current user and permissions even when the token is still fresh', async () => {
    mocks.getTokenPayload.mockReturnValue({ exp: Math.floor(Date.now() / 1000) + 3600 })
    mocks.get
      .mockResolvedValueOnce({ data: { data: adminUser } })
      .mockResolvedValueOnce({ data: { data: { permissions: ['admin:read'] } } })
    useAuthStore.setState({ user, isAuthenticated: true, permissions: [] })

    await authService.autoRefreshToken()

    expect(useAuthStore.getState().user).toEqual(adminUser)
    expect(useAuthStore.getState().permissions).toEqual(['admin:read'])
  })

  it('discards a mixed user-permission snapshot when generation changes mid-load', async () => {
    mocks.getTokenPayload.mockReturnValue({ exp: Math.floor(Date.now() / 1000) + 3600 })
    mocks.getSessionGeneration
      .mockReturnValueOnce('generation-alice')
      .mockReturnValueOnce('generation-bob')
    mocks.get
      .mockResolvedValueOnce({ data: { data: adminUser } })
      .mockResolvedValueOnce({ data: { data: { permissions: ['bob:read'] } } })
    useAuthStore.setState({ user, isAuthenticated: true, permissions: ['alice:read'] })

    await authService.autoRefreshToken()

    expect(useAuthStore.getState().user).toEqual(user)
    expect(useAuthStore.getState().permissions).toEqual(['alice:read'])
  })
})
