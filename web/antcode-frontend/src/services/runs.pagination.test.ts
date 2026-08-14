import { beforeEach, describe, expect, it, vi } from 'vitest'

import apiClient from './api'
import { runsService } from './runs'

vi.mock('./api', () => ({ default: { get: vi.fn() } }))

describe('runsService.listSpiderItems', () => {
  beforeEach(() => vi.clearAllMocks())

  it('forwards the stream cursor and preserves has_more', async () => {
    vi.mocked(apiClient.get).mockResolvedValue({
      data: { data: { items: [{ _id: '2-0' }], last_id: '2-0', count: 1, has_more: true } },
    })

    const result = await runsService.listSpiderItems('run-1', { startId: '1-0', count: 100 })

    expect(apiClient.get).toHaveBeenCalledWith('/api/v1/runs/run-1/spider-items', {
      params: { start_id: '1-0', count: 100 },
    })
    expect(result.has_more).toBe(true)
  })
})
