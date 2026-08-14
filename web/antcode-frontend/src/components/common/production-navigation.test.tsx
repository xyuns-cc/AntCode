import { MemoryRouter, useLocation } from 'react-router'
import { render, screen, waitFor } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import type { User } from '@/types'
import { AppRoutes } from '@/App'
import { createMenuItems } from './Layout/menuItems'

vi.mock('@/hooks/useAuth', () => ({
  useAuth: () => ({ isAuthenticated: true, loading: false }),
}))
vi.mock('@/components/common/Layout', async () => {
  const { Outlet } = await import('react-router')
  return { default: () => <Outlet /> }
})
vi.mock('@/components/common/AppInitializer', () => ({ default: () => null }))
vi.mock('@/utils/lazyLoad', () => ({ lazyLoad: () => () => null }))
vi.mock('antd', async (importOriginal) => {
  const actual = await importOriginal<typeof import('antd')>()
  return { ...actual, FloatButton: { BackTop: () => null } }
})

const adminUser: User = {
  id: 'admin-1',
  username: 'admin',
  is_active: true,
  is_admin: true,
  role: 'admin',
  created_at: '2026-07-13T00:00:00Z',
  updated_at: '2026-07-13T00:00:00Z',
}

const LocationProbe = () => {
  const location = useLocation()
  return <output aria-label="current route">{location.pathname}</output>
}

describe('production navigation surface', () => {
  it('redirects the removed cookie pool route to the dashboard', async () => {
    render(
      <MemoryRouter initialEntries={['/cookies']}>
        <AppRoutes />
        <LocationProbe />
      </MemoryRouter>,
    )

    await waitFor(() => {
      expect(screen.getByLabelText('current route')).toHaveTextContent('/dashboard')
    })
  })

  it.each([
    ['anonymous', null],
    ['administrator', adminUser],
    ['super administrator', { ...adminUser, role: 'super_admin' as const }],
  ])('does not expose the demo entry for %s', (_name, user) => {
    const menuItems = createMenuItems(user)

    expect(menuItems).not.toEqual(
      expect.arrayContaining([
        expect.objectContaining({ path: '/cookies' }),
      ]),
    )
    expect(menuItems.map((item) => item.label)).not.toContain('Cookie 账号池')
  })
})
