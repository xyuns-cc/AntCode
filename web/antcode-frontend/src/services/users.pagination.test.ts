import { beforeEach, describe, expect, it, vi } from 'vitest'

import apiClient from './api'
import { userService } from './users'

vi.mock('./api', () => ({
  default: { get: vi.fn() },
}))

const response = (items: Array<{ id: string }>, total: number) => ({
  data: {
    data: {
      items,
      pagination: { page: 1, size: 100, total, total_pages: Math.ceil(total / 100) },
    },
  },
})

describe('userService.getAllUsers', () => {
  beforeEach(() => vi.clearAllMocks())

  it('loads users after the first management page', async () => {
    vi.mocked(apiClient.get)
      .mockResolvedValueOnce(response([{ id: 'u-1' }], 2))
      .mockResolvedValueOnce(response([{ id: 'u-2' }], 2))

    const users = await userService.getAllUsers()

    expect(users.map((user) => user.id)).toEqual(['u-1', 'u-2'])
    expect(apiClient.get).toHaveBeenNthCalledWith(1, '/api/v1/users/', {
      params: { page: 1, size: 100 },
    })
    expect(apiClient.get).toHaveBeenNthCalledWith(2, '/api/v1/users/', {
      params: { page: 2, size: 100 },
    })
  })

  it('fails when the backend total cannot be reached', async () => {
    vi.mocked(apiClient.get)
      .mockResolvedValueOnce(response([{ id: 'u-1' }], 2))
      .mockResolvedValueOnce(response([], 2))

    await expect(userService.getAllUsers()).rejects.toThrow('用户分页响应提前结束')
  })
})
