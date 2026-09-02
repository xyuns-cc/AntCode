/**
 * 「HTTP 状态码分布」卡右下的 Success (2xx) / Error (4xx/5xx) 两格。
 *
 * 分母用响应数是**对的**：这是一张状态码分布卡，2xx 占比只对收到的响应才有定义，与
 * 规范意义上的「爬取成功率」（分子 2xx/3xx、分母请求总数，见 utils/spiderSuccessRate）
 * 是两件事，不要把两者统一。真正的问题只在空集：一条带状态码的响应都没有时，这两格
 * 曾渲染「0.0%」——而它们正上方的环形图这时画的是「暂无数据」，同一张卡自相矛盾。
 *
 * 判据成对：正例钉「真的全是 4xx 时 Success 就该是 0.0%」，反例钉「没有响应时不是 0.0%」。
 * 只有反例的话，一个恒显示 '—' 的实现也能过。
 */
import { render, screen, waitFor, within } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import type { ClusterSpiderStats } from '@/types'
import SpiderStatsTab from './SpiderStatsTab'

const mocks = vi.hoisted(() => ({
  getClusterSpiderStats: vi.fn(),
  getAllWorkers: vi.fn(),
  getWorkerSpiderStatsHistory: vi.fn(),
}))

vi.mock('react-chartjs-2', () => ({
  Bar: () => <div data-testid="bar-chart" />,
  Line: () => <div data-testid="line-chart" />,
  Doughnut: () => <div data-testid="doughnut-chart" />,
}))

vi.mock('@/services/workers', () => ({
  workerService: {
    getClusterSpiderStats: mocks.getClusterSpiderStats,
    getAllWorkers: mocks.getAllWorkers,
    getWorkerSpiderStatsHistory: mocks.getWorkerSpiderStatsHistory,
  },
}))

const share = async (label: string): Promise<string> => {
  const labelNode = await screen.findByText(label)
  const row = labelNode.parentElement
  if (!row) throw new Error(`「${label}」没有渲染出所属格子`)
  // 该格里只有标签和占比两个文本节点，去掉标签剩下的就是占比。
  return (row.textContent ?? '').replace(label, '').trim()
}

// 喂整份 ClusterSpiderStats。残缺 fixture 之前被调用点的 `|| 0` 兜住了：改成如实区分
// 「没取到」之后，缺字段会当场崩，说明这个 mock 一直在冒充一份后端不会返回的回包。
const clusterStats = (overrides: Partial<ClusterSpiderStats>): ClusterSpiderStats => ({
  totalRequests: 0,
  totalResponses: 0,
  totalItemsScraped: 0,
  totalErrors: 0,
  avgLatencyMs: 0,
  domainStats: [],
  clusterRequestsPerMinute: 0,
  workerCount: 1,
  statusCodes: {},
  ...overrides,
})

const renderTab = (statusCodes: Record<string, number>) => {
  mocks.getClusterSpiderStats.mockResolvedValue(clusterStats({ statusCodes }))
  mocks.getAllWorkers.mockResolvedValue([])
  return render(<SpiderStatsTab />)
}

describe('状态码分布卡的 2xx / 4xx-5xx 占比', () => {
  it('收到的响应全是 4xx 时 Success 就是 0.0%', async () => {
    renderTab({ '404': 3, '500': 1 })

    await waitFor(async () => expect(await share('Success (2xx)')).toBe('0.0%'))
    expect(await share('Error (4xx/5xx)')).toBe('100.0%')
  })

  it('分母是响应数而不是请求数', async () => {
    // 2 个 200 + 2 个 404 → 50.0%。若分母换成 totalRequests(=8) 就会算成 25.0%。
    mocks.getClusterSpiderStats.mockResolvedValue(clusterStats({
      statusCodes: { '200': 2, '404': 2 },
      totalRequests: 8,
    }))
    mocks.getAllWorkers.mockResolvedValue([])
    render(<SpiderStatsTab />)

    await waitFor(async () => expect(await share('Success (2xx)')).toBe('50.0%'))
  })

  it('一条响应都没有时是占位符，不是 0.0%', async () => {
    renderTab({})

    // 上方环形图这时给的是「暂无数据」，两格必须跟它一致。
    await waitFor(async () => expect(await share('Success (2xx)')).toBe('—'))
    expect(await share('Error (4xx/5xx)')).toBe('—')
    const chartCard = await screen.findByText('HTTP 状态码分布')
    expect(within(chartCard.closest('.ant-card') as HTMLElement).getAllByText('暂无数据').length).toBeGreaterThan(0)
  })
})
