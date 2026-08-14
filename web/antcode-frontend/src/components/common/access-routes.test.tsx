import type React from 'react'
import { MemoryRouter, Route, Routes } from 'react-router'
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import type { User } from '@/types'
import { useAuth } from '@/hooks/useAuth'
import AdminRoute from './AdminRoute'
import AuthGuard from './AuthGuard'
import SuperAdminRoute from './SuperAdminRoute'

const guardMocks = vi.hoisted(() => ({
  getAccessToken: vi.fn(),
  isTokenExpired: vi.fn(),
  handleAuthFailure: vi.fn(),
}))

vi.mock('@/hooks/useAuth', () => ({ useAuth: vi.fn() }))
vi.mock('@/services/api', () => ({
  TokenManager: {
    getAccessToken: guardMocks.getAccessToken,
    isTokenExpired: guardMocks.isTokenExpired,
  },
}))
vi.mock('@/utils/authHandler', () => ({
  AuthHandler: { handleAuthFailure: guardMocks.handleAuthFailure },
}))

const mockedUseAuth = vi.mocked(useAuth)
const baseUser: User = {
  id: 'user-1',
  username: 'alice',
  is_active: true,
  is_admin: false,
  role: 'user',
  created_at: '2026-07-13T00:00:00Z',
  updated_at: '2026-07-13T00:00:00Z',
}

const setAuth = (isAuthenticated: boolean, user: User | null) => {
  mockedUseAuth.mockReturnValue({ isAuthenticated, user } as ReturnType<typeof useAuth>)
}

const renderProtected = (guard: React.ReactNode) => {
  return render(
    <MemoryRouter initialEntries={['/protected']}>
      <Routes>
        <Route path="/login" element={<div>登录页面</div>} />
        <Route path="/protected" element={guard} />
      </Routes>
    </MemoryRouter>,
  )
}

describe('privileged routes', () => {
  beforeEach(() => {
    setAuth(false, null)
    guardMocks.getAccessToken.mockReturnValue('valid-token')
    guardMocks.isTokenExpired.mockReturnValue(false)
  })

  it('invalidates the session when a visible page has lost its memory token', () => {
    setAuth(true, baseUser)
    guardMocks.getAccessToken.mockReturnValue(null)
    Object.defineProperty(document, 'visibilityState', {
      configurable: true,
      value: 'visible',
    })

    render(<AuthGuard><div>受保护内容</div></AuthGuard>)
    document.dispatchEvent(new Event('visibilitychange'))

    expect(screen.getByText('受保护内容')).toBeInTheDocument()
    expect(guardMocks.handleAuthFailure).toHaveBeenCalledOnce()
  })

  it('redirects unauthenticated users to login', () => {
    renderProtected(<AdminRoute><div>管理员内容</div></AdminRoute>)

    expect(screen.getByText('登录页面')).toBeInTheDocument()
    expect(screen.queryByText('管理员内容')).not.toBeInTheDocument()
  })

  it('shows 403 and lets a regular user go back', async () => {
    setAuth(true, baseUser)
    const back = vi.spyOn(window.history, 'back').mockImplementation(() => undefined)
    renderProtected(<AdminRoute><div>管理员内容</div></AdminRoute>)

    expect(screen.getByText('抱歉，您没有权限访问此页面。')).toBeInTheDocument()
    await userEvent.setup().click(screen.getByRole('button', { name: /返\s*回/ }))
    expect(back).toHaveBeenCalledOnce()
  })

  it('allows administrators through the admin guard', () => {
    setAuth(true, { ...baseUser, is_admin: true, role: 'admin' })
    renderProtected(<AdminRoute><div>管理员内容</div></AdminRoute>)

    expect(screen.getByText('管理员内容')).toBeInTheDocument()
  })

  it('requires the explicit super_admin role', () => {
    setAuth(true, { ...baseUser, is_admin: true, role: 'admin' })
    const { rerender } = renderProtected(
      <SuperAdminRoute><div>超级管理员内容</div></SuperAdminRoute>,
    )
    expect(screen.getByText(/仅限超级管理员/)).toBeInTheDocument()

    setAuth(true, { ...baseUser, is_admin: true, role: 'super_admin' })
    rerender(
      <MemoryRouter initialEntries={['/protected']}>
        <SuperAdminRoute><div>超级管理员内容</div></SuperAdminRoute>
      </MemoryRouter>,
    )
    expect(screen.getByText('超级管理员内容')).toBeInTheDocument()
  })
})
