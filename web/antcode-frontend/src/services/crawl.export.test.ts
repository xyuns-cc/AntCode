import type { AxiosRequestConfig } from 'axios'
import { describe, expect, it, vi } from 'vitest'

vi.mock('./api', () => ({ default: { get: vi.fn() } }))

import { crawlService } from './crawl'

interface CrawlDownloadAccess {
  downloadFile: (url: string, filename?: string, config?: AxiosRequestConfig) => Promise<void>
}

describe('crawl batch export contract', () => {
  it('does not expose a delete operation missing from the backend lifecycle', () => {
    expect(crawlService).not.toHaveProperty('deleteBatch')
  })

  it('disables the generic request timeout for a complete download', async () => {
    const service = crawlService as unknown as CrawlDownloadAccess
    const download = vi.spyOn(service, 'downloadFile').mockResolvedValue(undefined)

    await crawlService.exportBatch('batch-1', 'csv')

    expect(download).toHaveBeenCalledWith(
      '/batches/batch-1/export?format=csv',
      'crawl-batch-batch-1.csv',
      { timeout: 0 }
    )
  })
})
