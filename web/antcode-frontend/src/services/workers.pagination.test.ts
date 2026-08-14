import { beforeEach, describe, expect, it, vi } from 'vitest'

import apiClient from './api'
import { workerService } from './workers'

vi.mock('./api', () => ({
  default: { get: vi.fn() },
}))

const response = (items: Array<{ id: string }>, total: number) => ({
  data: { data: { items, total, page: 1, size: 100 } },
})

describe('workerService.getAllWorkers', () => {
  beforeEach(() => vi.clearAllMocks())

  it('reads every backend page without a fixed result cap', async () => {
    vi.mocked(apiClient.get)
      .mockResolvedValueOnce(response([{ id: 'w-1' }], 2))
      .mockResolvedValueOnce(response([{ id: 'w-2' }], 2))

    const workers = await workerService.getAllWorkers()

    expect(workers.map((worker) => worker.id)).toEqual(['w-1', 'w-2'])
    expect(apiClient.get).toHaveBeenNthCalledWith(1, '/api/v1/workers', {
      params: { page: 1, size: 100 },
    })
    expect(apiClient.get).toHaveBeenNthCalledWith(2, '/api/v1/workers', {
      params: { page: 2, size: 100 },
    })
  })

  it('fails explicitly when pagination ends before the declared total', async () => {
    vi.mocked(apiClient.get)
      .mockResolvedValueOnce(response([{ id: 'w-1' }], 2))
      .mockResolvedValueOnce(response([], 2))

    await expect(workerService.getAllWorkers()).rejects.toThrow('Worker 分页响应提前结束')
  })

  it('keeps caller filters and cancellation while owning pagination fields', async () => {
    vi.mocked(apiClient.get).mockResolvedValueOnce(response([{ id: 'w-1' }], 1))
    const controller = new AbortController()

    await workerService.getAllWorkers({
      signal: controller.signal,
      params: { status: 'online', page: 9, size: 1 },
    })

    expect(apiClient.get).toHaveBeenCalledWith('/api/v1/workers', {
      signal: controller.signal,
      params: { status: 'online', page: 1, size: 100 },
    })
  })
})
