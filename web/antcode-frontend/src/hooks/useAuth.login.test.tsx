import type React from 'react'
import { MemoryRouter } from 'react-router-dom'
import { act, renderHook, waitFor } from '@testing-library/react'
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
  clearAccessToken,
  clearSessionGeneration,
  getAccessToken,
  getSessionGeneration,
  setAccessToken,
  setSessionGeneration,
  setSessionHint,
} from '@/services/authToken'
import { resetSessionRestore, useAuth } from './useAuth'

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

describe('useAuth login settlement', () => {
  beforeEach(() => {
    vi.resetAllMocks()
    clearAccessToken()
    clearSessionGeneration()
    resetSessionRestore()
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

  it('clears the whole local session when login permission loading fails', async () => {
    const { result } = renderHook(() => useAuth(), { wrapper })
    await waitFor(() => expect(result.current.loading).toBe(false))
    setAccessToken(accessToken(replacementUser.username))
    serviceMocks.login.mockImplementation(async () => {
      setSessionGeneration(`session-${replacementUser.username}`, replacementUser.username)
      return {
        access_token: getAccessToken(),
        token_type: 'bearer',
        expires_in: 3600,
        user: replacementUser,
      }
    })
    serviceMocks.logout.mockImplementation(async () => {
      clearAccessToken()
      clearSessionGeneration()
    })
    serviceMocks.getUserPermissions.mockRejectedValue(new Error('permissions unavailable'))

    await expect(act(() => result.current.login({
      username: replacementUser.username,
      password: 'password',
    }))).rejects.toThrow('permissions unavailable')

    expect(getAccessToken()).toBeNull()
    expect(useAuthStore.getState().user).toBeNull()
    expect(useAuthStore.getState().isAuthenticated).toBe(false)
    expect(serviceMocks.logout).toHaveBeenCalledOnce()
  })

  it('does not revoke a newer account when an older login fails to initialize', async () => {
    const { result } = renderHook(() => useAuth(), { wrapper })
    await waitFor(() => expect(result.current.loading).toBe(false))
    const replacementToken = accessToken(replacementUser.username)
    serviceMocks.login.mockImplementation(async () => {
      setAccessToken(replacementToken)
      setSessionGeneration(`session-${replacementUser.username}`, replacementUser.username)
      return {
        access_token: replacementToken,
        token_type: 'bearer',
        expires_in: 3600,
        user: replacementUser,
      }
    })
    serviceMocks.getUserPermissions.mockImplementation(async () => {
      setSessionGeneration('session-newer', 'newer-account')
      throw new Error('permissions unavailable')
    })

    await expect(act(() => result.current.login({
      username: replacementUser.username,
      password: 'password',
    }))).rejects.toThrow('permissions unavailable')

    expect(serviceMocks.logout).not.toHaveBeenCalled()
    expect(getSessionGeneration()).toBe('session-newer')
    expect(useAuthStore.getState().user).toEqual(existingUser)
  })

  it('does not clear another tab session after invalid login credentials', async () => {
    const { result } = renderHook(() => useAuth(), { wrapper })
    await waitFor(() => expect(result.current.loading).toBe(false))
    setAccessToken(accessToken(existingUser.username))
    setSessionGeneration(`session-${existingUser.username}`, existingUser.username)
    serviceMocks.login.mockRejectedValue(new Error('用户名或密码错误'))

    await expect(act(() => result.current.login({
      username: 'other',
      password: 'wrong',
    }))).rejects.toThrow('用户名或密码错误')

    expect(getAccessToken()).not.toBeNull()
    expect(getSessionGeneration()).toBe(`session-${existingUser.username}`)
    expect(useAuthStore.getState().user).toEqual(existingUser)
  })

  it('keeps the authenticated state when the server cannot confirm logout', async () => {
    const { result } = renderHook(() => useAuth(), { wrapper })
    await waitFor(() => expect(result.current.loading).toBe(false))
    serviceMocks.logout.mockRejectedValue(new Error('logout unavailable'))

    await expect(act(() => result.current.logout())).rejects.toThrow('logout unavailable')

    expect(useAuthStore.getState().user).toEqual(existingUser)
    expect(useAuthStore.getState().isAuthenticated).toBe(true)
  })
})
