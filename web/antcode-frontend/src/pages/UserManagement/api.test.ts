import { beforeEach, describe, expect, it, vi } from 'vitest'

const mocks = vi.hoisted(() => ({ get: vi.fn(), post: vi.fn(), put: vi.fn(), delete: vi.fn() }))
vi.mock('@/services/api', () => ({ default: mocks }))

import { userManagementApi } from './api'

describe('userManagementApi', () => {
  beforeEach(() => {
    const response = { data: { success: true, message: 'ok', data: null } }
    mocks.post.mockResolvedValue(response)
    mocks.put.mockResolvedValue(response)
    mocks.get.mockResolvedValue(response)
  })

  it('uses the real session revocation endpoint', async () => {
    mocks.post.mockResolvedValue({
      data: { success: true, message: 'ok', data: { revoked_sessions: 2 } },
    })

    await expect(userManagementApi.revokeSessions('user-2')).resolves.toEqual({
      revoked_sessions: 2,
    })
    expect(mocks.post).toHaveBeenCalledWith('/api/v1/users/user-2/kick')
  })

  it('updates profile without forbidden role or password fields', async () => {
    const values = { username: 'alice', email: 'a@example.com', is_active: true, is_admin: true }

    await userManagementApi.updateProfile('user-2', values)

    expect(mocks.put).toHaveBeenCalledWith('/api/v1/users/user-2', {
      username: 'alice',
      email: 'a@example.com',
      is_active: true,
    })
  })

  it('creates administrators with a role consistent with is_admin', async () => {
    const values = { username: 'alice', password: 'Strong#123', is_active: true, is_admin: true }

    await userManagementApi.create(values)

    expect(mocks.post).toHaveBeenCalledWith('/api/v1/users/', { ...values, role: 'admin' })
  })

  it('sends search to the server before pagination', async () => {
    mocks.get.mockResolvedValue({
      data: {
        success: true,
        message: 'ok',
        data: { items: [], pagination: { page: 1, size: 20, total: 0, pages: 0 } },
      },
    })

    await userManagementApi.list({
      page: 1,
      size: 20,
      search: 'user-on-page-two',
      sortField: null,
      sortOrder: 'asc',
    })

    expect(mocks.get).toHaveBeenCalledWith('/api/v1/users/', {
      params: {
        page: 1,
        size: 20,
        search: 'user-on-page-two',
        sort_by: undefined,
        sort_order: undefined,
      },
    })
  })
})
