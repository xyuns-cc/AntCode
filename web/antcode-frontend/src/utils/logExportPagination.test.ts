import { beforeEach, describe, expect, it, vi } from 'vitest'

import { logService } from '@/services/logs'
import { fetchLogEntriesForExport } from '@/utils/logExportPagination'

vi.mock('@/services/logs', () => ({
  logService: { getRunLogs: vi.fn() },
}))

function page(pageNumber: number, total: number, itemCount: number) {
  return {
    success: true,
    code: 200,
    message: 'ok',
    data: {
      total,
      page: pageNumber,
      size: 32,
      items: Array.from({ length: itemCount }, (_, index) => ({
        id: `${pageNumber}-${index}`,
        timestamp: '2026-08-12T00:00:00Z',
        level: 'INFO' as const,
        log_type: 'stdout' as const,
        message: `line-${pageNumber}-${index}`,
      })),
    },
  }
}

describe('fetchLogEntriesForExport', () => {
  beforeEach(() => vi.clearAllMocks())

  it('逐页读取结构化日志且不超过 maxLines', async () => {
    vi.mocked(logService.getRunLogs)
      .mockResolvedValueOnce(page(1, 100, 32))
      .mockResolvedValueOnce(page(2, 100, 32))
      .mockResolvedValueOnce(page(3, 100, 32))

    const entries = await fetchLogEntriesForExport('run-1', 70)

    expect(entries).toHaveLength(70)
    expect(logService.getRunLogs).toHaveBeenNthCalledWith(1, 'run-1', { page: 1, size: 32 })
    expect(logService.getRunLogs).toHaveBeenNthCalledWith(3, 'run-1', { page: 3, size: 32 })
  })

  it('服务端提前返回空页时显式失败', async () => {
    vi.mocked(logService.getRunLogs)
      .mockResolvedValueOnce(page(1, 40, 32))
      .mockResolvedValueOnce(page(2, 40, 0))

    await expect(fetchLogEntriesForExport('run-1', 100)).rejects.toThrow('日志分页提前结束')
  })

  it('maxLines 小于单页上限时只请求所需数量', async () => {
    const response = page(1, 100, 10)
    response.data.size = 10
    vi.mocked(logService.getRunLogs).mockResolvedValueOnce(response)

    const entries = await fetchLogEntriesForExport('run-1', 10)

    expect(entries).toHaveLength(10)
    expect(logService.getRunLogs).toHaveBeenCalledWith('run-1', { page: 1, size: 10 })
  })

  it('跨页重复条目时显式失败', async () => {
    const secondPage = page(2, 40, 8)
    secondPage.data.items[0].id = '1-0'
    vi.mocked(logService.getRunLogs)
      .mockResolvedValueOnce(page(1, 40, 32))
      .mockResolvedValueOnce(secondPage)

    await expect(fetchLogEntriesForExport('run-1', 100)).rejects.toThrow('日志分页响应包含重复条目')
  })
})
