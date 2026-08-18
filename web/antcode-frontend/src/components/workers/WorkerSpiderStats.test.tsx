/**
 * 成功率的渲染必须真的用上统一口径，而不只是工具函数里正确。
 *
 * 用走查现场那组数据（responseCount 远小于 errorCount）驱动组件：旧公式
 * `(responseCount - errorCount) / responseCount * 100` 会把 `-500.0%` 画进
 * antd 圆环，本用例断言页面上不出现负号。
 */
import { render, screen, waitFor } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import type { SpiderStatsSummary } from '@/types'
import { workerService } from '@/services/workers'
import WorkerSpiderStats from './WorkerSpiderStats'

const FIELD_DATA: SpiderStatsSummary = {
  requestCount: 2,
  responseCount: 2,
  itemScrapedCount: 0,
  errorCount: 12,
  avgLatencyMs: 306.86,
  requestsPerMinute: 0,
  statusCodes: { '200': 2 }
}

const EMPTY_DATA: SpiderStatsSummary = {
  requestCount: 0,
  responseCount: 0,
  itemScrapedCount: 0,
  errorCount: 0,
  avgLatencyMs: 0,
  requestsPerMinute: 0,
  statusCodes: {}
}

function renderWithStats(stats: SpiderStatsSummary) {
  vi.spyOn(workerService, 'getWorkerSpiderStats').mockResolvedValue(stats)
  return render(<WorkerSpiderStats workerId="worker-1" workerStatus="online" />)
}

describe('WorkerSpiderStats 成功率', () => {
  it('errorCount 大于 responseCount 时不再渲染负百分比', async () => {
    const { container } = renderWithStats(FIELD_DATA)

    await waitFor(() => expect(screen.getByText('成功率')).toBeInTheDocument())
    expect(container.textContent).toContain('100.0%')
    expect(container.textContent).not.toContain('-500')
    expect(container.textContent).not.toMatch(/-\d+(\.\d+)?%/)
  })

  it('一个请求都没发过时显示占位而不是 0%', async () => {
    const { container } = renderWithStats(EMPTY_DATA)

    await waitFor(() => expect(screen.getByText('成功率')).toBeInTheDocument())
    expect(container.textContent).toContain('—')
  })
})
