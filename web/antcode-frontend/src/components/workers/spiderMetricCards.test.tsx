/**
 * 爬虫统计四张核心指标卡 + 顶部「N Worker 在线」。
 *
 * 这些位置过去一律写 `stats?.X || 0`：一次 /workers/cluster/spider-stats 失败会渲染成
 * 「请求 0、抓取 0、延迟 0ms、错误 0」外加一颗绿灯，读起来就是「集群很闲、一切正常」。
 *
 * 判据成对，且**走完整个 SpiderStatsTab**而不是单测 MetricCard：本仓抓到过「底层修好、
 * 调用点仍写 || 0」这类假绿，只有从组件外部喂 service 的返回值才钉得住调用点。
 */
import { render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
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

// 卡片结构：内容区第一个子节点是标题，第二个是「数值 + 单位」那一行。
const metricValue = async (title: string): Promise<string> => {
  const titleNode = await screen.findByText(title)
  const content = titleNode.parentElement
  if (!content) throw new Error(`「${title}」没有渲染出内容区`)
  const valueRow = content.children[1]
  if (!valueRow) throw new Error(`「${title}」没有渲染出数值行`)
  return (valueRow.textContent ?? '').trim()
}

const allZeroStats = {
  totalRequests: 0,
  totalResponses: 0,
  totalItemsScraped: 0,
  totalErrors: 0,
  avgLatencyMs: 0,
  domainStats: [],
  clusterRequestsPerMinute: 0,
  workerCount: 0,
  statusCodes: {},
}

describe('爬虫指标卡区分「读数是 0」与「这一轮没取到」', () => {
  it('后端真的回了全 0 时四张卡就显示 0', async () => {
    mocks.getClusterSpiderStats.mockResolvedValue(allZeroStats)
    mocks.getAllWorkers.mockResolvedValue([])

    render(<SpiderStatsTab />)

    expect(await metricValue('最近完成请求量 (60秒)')).toBe('0')
    expect(await metricValue('抓取数据总数')).toBe('0')
    expect(await metricValue('平均响应延迟')).toBe('0ms')
    expect(await metricValue('异常 & 错误')).toBe('0')
    expect(await screen.findByText('0 Worker 在线')).toBeInTheDocument()
    expect(document.querySelector('.ant-badge-status-success')).toBeInTheDocument()
  })

  it('拉取失败时四张卡是占位符，不是 0', async () => {
    mocks.getClusterSpiderStats.mockRejectedValue(new Error('503'))
    mocks.getAllWorkers.mockResolvedValue([])

    render(<SpiderStatsTab />)

    // 旧实现在这里给的是 '0' / '0ms' / '0 Worker 在线'，与上面那条真·全 0 无法区分。
    expect(await metricValue('异常 & 错误')).toBe('—')
    expect(await metricValue('最近完成请求量 (60秒)')).toBe('—')
    expect(await metricValue('抓取数据总数')).toBe('—')
    expect(await metricValue('平均响应延迟')).toBe('—ms')
    expect(await screen.findByText('— Worker 在线')).toBeInTheDocument()
    expect(screen.queryByText('0 Worker 在线')).not.toBeInTheDocument()
    // 顶部那颗绿灯也不能亮：绿灯 + 「0 Worker 在线」同样是一次「正常读数」的样子。
    expect(document.querySelector('.ant-badge-status-success')).not.toBeInTheDocument()
  })
})
