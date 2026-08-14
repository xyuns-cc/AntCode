import { beforeEach, describe, expect, it, vi } from 'vitest'

import apiClient from './api'
import { logService } from './logs'

vi.mock('./api', () => ({
  default: { get: vi.fn(), post: vi.fn() },
}))

describe('logService.getRunLogs', () => {
  beforeEach(() => vi.clearAllMocks())

  it('向结构化日志端点传递分页参数并限制单页条目数', async () => {
    vi.mocked(apiClient.get).mockResolvedValue({
      data: {
        data: {
          run_id: 'run-1',
          format: 'structured',
          structured_data: { total: 0, page: 3, size: 32, items: [] },
        },
      },
    })

    const response = await logService.getRunLogs('run-1', { page: 3, size: 999 })

    expect(apiClient.get).toHaveBeenCalledWith('/api/v1/logs/runs/run-1', {
      params: { format: 'structured', page: 3, size: 32 },
    })
    expect(response.data).toEqual({ total: 0, page: 3, size: 32, items: [] })
  })
})
