import type React from 'react'
import { MemoryRouter } from 'react-router-dom'
import { renderHook, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import type { User } from '@/types'
import { useAuthStore } from '@/stores/authStore'

const serviceMocks = vi.hoisted(() => ({
  restoreSession: vi.fn(),
  autoRefreshToken: vi.fn(),
  login: vi.fn(),
  logout: vi.fn(),
  getCurrentUser: vi.fn(),
  updateUser: vi.fn(),
  changePassword: vi.fn(),
  refreshToken: vi.fn(),
  getUserPermissions: vi.fn(),
  requestPeerAccessToken: vi.fn(),
}))

vi.mock('@/services/auth', () => ({ authService: serviceMocks }))
vi.mock('@/services/authSessionChannel', () => ({
  requestPeerAccessToken: serviceMocks.requestPeerAccessToken,
  subscribeAccessTokenUpdates: vi.fn(),
}))
vi.mock('@/utils/logger', () => ({
  default: { warn: vi.fn(), info: vi.fn(), error: vi.fn() },
}))

import {
  getAccessToken,
  getSessionGeneration,
  clearAccessToken,
  clearSessionGeneration,
  setAccessToken,
  setSessionGeneration,
  setSessionHint,
} from '@/services/authToken'
import {
  ensureSessionRestored,
  handleAuthBroadcast,
  handlePeerAccessTokenUpdate,
} from '@/services/authSessionRestore'
import { useAuth, resetSessionRestore } from './useAuth'

const existingUser: User = {
  id: 'user-existing',
  username: 'existing',
  is_active: true,
  is_admin: false,
  role: 'user',
  created_at: '2026-07-13T00:00:00Z',
  updated_at: '2026-07-13T00:00:00Z',
}

const replacementUser: User = {
  ...existingUser,
  id: 'user-replacement',
  username: 'replacement',
  role: 'admin',
  is_admin: true,
}

const accessToken = (username: string): string => {
  const payload = btoa(JSON.stringify({
    exp: Math.floor(Date.now() / 1000) + 3600,
    username,
    token_type: 'access',
    session_jti: `session-${username}`,
  }))
  return `header.${payload}.signature`
}

const wrapper = ({ children }: { children: React.ReactNode }) => (
  <MemoryRouter>{children}</MemoryRouter>
)

describe('useAuth session restoration', () => {
  beforeEach(() => {
    vi.resetAllMocks()
    clearAccessToken()
    clearSessionGeneration()
    // 会话恢复是模块级单飞（每次应用加载只执行一次），测试间需要重置
    resetSessionRestore()
    // 设置“存在会话”提示标记，否则匿名短路会跳过网络恢复
    setSessionHint()
    serviceMocks.restoreSession.mockResolvedValue(existingUser)
    serviceMocks.getUserPermissions.mockResolvedValue([])
    serviceMocks.requestPeerAccessToken.mockResolvedValue(null)
    useAuthStore.setState({
      user: existingUser,
      isAuthenticated: true,
      isLoading: false,
      error: null,
      permissions: [],
    })
  })

  it('clears stale authenticated state when cookie restoration fails', async () => {
    serviceMocks.restoreSession.mockRejectedValue(new Error('session expired'))

    const { result } = renderHook(() => useAuth(), { wrapper })

    await waitFor(() => expect(result.current.loading).toBe(false))
    expect(useAuthStore.getState().user).toBeNull()
    expect(useAuthStore.getState().isAuthenticated).toBe(false)
  })

  it('loads authoritative permissions while restoring the session', async () => {
    serviceMocks.restoreSession.mockResolvedValue(existingUser)
    serviceMocks.getUserPermissions.mockResolvedValue(['user:read', 'project:read'])

    const { result } = renderHook(() => useAuth(), { wrapper })

    await waitFor(() => expect(result.current.loading).toBe(false))
    expect(result.current.permissions).toEqual(['user:read', 'project:read'])
    expect(serviceMocks.getUserPermissions).toHaveBeenCalledOnce()
  })

  it('surfaces permission loading failures and clears partial authentication', async () => {
    const payload = btoa(JSON.stringify({ exp: Math.floor(Date.now() / 1000) + 3600 }))
    setAccessToken(`header.${payload}.signature`)
    serviceMocks.restoreSession.mockResolvedValue(existingUser)
    serviceMocks.getUserPermissions.mockRejectedValue(new Error('permission service unavailable'))

    const { result } = renderHook(() => useAuth(), { wrapper })

    await waitFor(() => expect(result.current.loading).toBe(false))
    expect(result.current.error).toBe('permission service unavailable')
    expect(result.current.isAuthenticated).toBe(false)
    expect(getAccessToken()).toBeNull()
  })

  it('reloads user and permissions when another tab logs into a different account', async () => {
    setAccessToken(accessToken(existingUser.username))
    serviceMocks.requestPeerAccessToken.mockImplementation(async () => {
      setSessionGeneration(`session-${replacementUser.username}`)
      setAccessToken(accessToken(replacementUser.username))
      return getAccessToken()
    })
    serviceMocks.restoreSession.mockResolvedValue(replacementUser)
    serviceMocks.getUserPermissions.mockResolvedValue(['admin:read'])
    setSessionGeneration(`session-${replacementUser.username}`)

    await handleAuthBroadcast({
      type: 'login',
      at: Date.now(),
      username: replacementUser.username,
    })

    expect(serviceMocks.requestPeerAccessToken).toHaveBeenCalledWith({
      sessionJti: `session-${replacementUser.username}`,
      username: replacementUser.username,
    })
    expect(useAuthStore.getState().user).toEqual(replacementUser)
    expect(useAuthStore.getState().permissions).toEqual(['admin:read'])
  })

  it('queues a forced account reload behind an in-flight restoration', async () => {
    let finishFirstRestore: ((user: User) => void) | undefined
    const firstRestore = new Promise<User>((resolve) => {
      finishFirstRestore = resolve
    })
    serviceMocks.restoreSession
      .mockImplementationOnce(() => firstRestore)
      .mockResolvedValueOnce(replacementUser)
    serviceMocks.requestPeerAccessToken.mockImplementation(async () => {
      setSessionGeneration(`session-${replacementUser.username}`)
      setAccessToken(accessToken(replacementUser.username))
      return getAccessToken()
    })

    const initialRestore = ensureSessionRestored()
    setSessionGeneration(`session-${replacementUser.username}`)
    const accountReload = handleAuthBroadcast({
      type: 'login',
      at: Date.now(),
      username: replacementUser.username,
    })
    finishFirstRestore?.(existingUser)
    await Promise.all([initialRestore, accountReload])

    expect(serviceMocks.restoreSession).toHaveBeenCalledTimes(2)
    expect(useAuthStore.getState().user).toEqual(replacementUser)
  })

  it('drains an account reload requested while the queued restore is running', async () => {
    let finishFirstRestore: ((user: User) => void) | undefined
    let finishSecondRestore: ((user: User) => void) | undefined
    const firstRestore = new Promise<User>((resolve) => {
      finishFirstRestore = resolve
    })
    const secondRestore = new Promise<User>((resolve) => {
      finishSecondRestore = resolve
    })
    serviceMocks.restoreSession
      .mockImplementationOnce(() => firstRestore)
      .mockImplementationOnce(() => secondRestore)
      .mockResolvedValueOnce(replacementUser)

    const initialRestore = ensureSessionRestored()
    const secondRequest = ensureSessionRestored({ force: true })
    finishFirstRestore?.(existingUser)
    await waitFor(() => expect(serviceMocks.restoreSession).toHaveBeenCalledTimes(2))
    const thirdRequest = ensureSessionRestored({ force: true })
    finishSecondRestore?.(existingUser)
    await Promise.all([initialRestore, secondRequest, thirdRequest])

    expect(serviceMocks.restoreSession).toHaveBeenCalledTimes(3)
    expect(useAuthStore.getState().user).toEqual(replacementUser)
  })

  it('discards a restore snapshot when the shared generation changes mid-load', async () => {
    setSessionGeneration('session-existing', existingUser.username)
    useAuthStore.setState({ permissions: ['existing:read'] })
    serviceMocks.getUserPermissions.mockImplementation(async () => {
      setSessionGeneration('session-replacement', replacementUser.username)
      return ['replacement:read']
    })

    await ensureSessionRestored({ force: true })

    expect(useAuthStore.getState().user).toEqual(existingUser)
    expect(useAuthStore.getState().permissions).toEqual(['existing:read'])
  })

  it('does not clear a newer session when an older restore fails', async () => {
    const oldToken = accessToken(existingUser.username)
    const newToken = accessToken(replacementUser.username)
    setAccessToken(oldToken)
    setSessionGeneration(`session-${existingUser.username}`, existingUser.username)
    serviceMocks.restoreSession.mockImplementation(async () => {
      setAccessToken(newToken)
      setSessionGeneration(`session-${replacementUser.username}`, replacementUser.username)
      throw new Error('old restore failed')
    })

    await ensureSessionRestored({ force: true })

    expect(getAccessToken()).toBe(newToken)
    expect(getSessionGeneration()).toBe(`session-${replacementUser.username}`)
    expect(useAuthStore.getState().user).toEqual(existingUser)
  })

  it('reloads role and permissions after a same-account token update', async () => {
    const promotedUser = { ...existingUser, role: 'admin' as const, is_admin: true }
    serviceMocks.restoreSession.mockResolvedValue(promotedUser)
    serviceMocks.getUserPermissions.mockResolvedValue(['admin:read'])
    setAccessToken(accessToken(existingUser.username))

    await handlePeerAccessTokenUpdate(accessToken(existingUser.username))

    expect(useAuthStore.getState().user).toEqual(promotedUser)
    expect(useAuthStore.getState().permissions).toEqual(['admin:read'])
  })
})
