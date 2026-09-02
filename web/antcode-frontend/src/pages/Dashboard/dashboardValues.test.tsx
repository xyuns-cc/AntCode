/**
 * 两组缺陷在同一张页面上，用同一套渲染钉住：
 *
 * 1. 「今日完成任务」卡的数值取的是 /dashboard/summary 的 `tasks.by_status.success`
 *    （= `Task.filter(status=SUCCESS).count()`：任务定义的当前状态、**全时段**），而同一
 *    张卡的副标题「成功率」取的是 /dashboard/metrics 的 `success_rate`（**当日**、按 run
 *    算）。一张卡上两个时间范围两种统计对象。
 * 2. 5 个请求里失败的那几路仍按 `?? 0` 渲染。/dashboard/metrics 与 /workers/stats 都是
 *    管理员专属，普通用户稳定 403 —— 于是他们看到的是「0/0 Worker 就绪、队列 0、CPU 0%」，
 *    一块看起来一切正常的死板。
 *
 * 判据成对：全成功那条钉「必须显示对的值」，部分失败那条钉「必须不显示 0」，并且在同一次
 * 渲染里留一个真的是 0 的字段，证明占位符没有把 0 一起吞掉。
 */
import { render, screen, waitFor, within } from '@testing-library/react'
import { MemoryRouter } from 'react-router'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import Dashboard from './index'

const mocks = vi.hoisted(() => ({
  getDashboardStats: vi.fn(),
  getSystemMetrics: vi.fn(),
  getHourlyTrend: vi.fn(),
  getAggregateStats: vi.fn(),
  getClusterSpiderStats: vi.fn(),
  getAllWorkers: vi.fn(),
  getWorkerSpiderStatsHistory: vi.fn(),
}))

// chart.js 需要 canvas；SpiderStatsTab 是静态 import，模块加载就会拉进来。
vi.mock('react-chartjs-2', () => ({
  Bar: () => <div data-testid="bar-chart" />,
  Line: () => <div data-testid="line-chart" />,
  Doughnut: () => <div data-testid="doughnut-chart" />,
}))

vi.mock('@/services/dashboard', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/services/dashboard')>()),
  dashboardService: {
    getDashboardStats: mocks.getDashboardStats,
    getSystemMetrics: mocks.getSystemMetrics,
    getHourlyTrend: mocks.getHourlyTrend,
  },
}))

vi.mock('@/services/workers', () => ({
  workerService: {
    getAggregateStats: mocks.getAggregateStats,
    getClusterSpiderStats: mocks.getClusterSpiderStats,
    getAllWorkers: mocks.getAllWorkers,
    getWorkerSpiderStatsHistory: mocks.getWorkerSpiderStatsHistory,
  },
}))

// 全时段的成功任务数远大于 24 小时窗口，两者不会互相冒充。
const SUMMARY = {
  projects: { total: 7, active: 5, inactive: 2 },
  tasks: { total: 900, active: 12, running: 4, success: 999, failed: 888 },
}

// 24 小时内 9 成 1 败 → 成功率 90.0%，与 metrics.success_rate 的 42 明显不同。
const TREND = [
  { hour: 0, tasks: 4, success: 3, failed: 1 },
  { hour: 1, tasks: 6, success: 6, failed: 0 },
]

const METRICS = {
  active_tasks: 1,
  total_executions: 2,
  success_rate: 42,
  queue_size: 17,
  cpu_usage: { percent: 63, cores: 8 },
  memory_usage: { total: 1, used: 1, available: 0, percent: 71 },
  disk_usage: { total: 1, used: 1, free: 0, percent: 12 },
  uptime: 3,
}

const WORKER_STATS = { onlineWorkers: 3, totalWorkers: 4 }

const renderDashboard = () => render(<MemoryRouter><Dashboard /></MemoryRouter>)

// StatCard 把标题 / 数值 / 副标题放在同一个 content 容器里；按标题定位到那一张卡再断言，
// 避免整页子串匹配被别处的数字蒙混过关。
const card = async (title: string): Promise<HTMLElement> => {
  const titleNode = await screen.findByText(title)
  const container = titleNode.parentElement
  if (!container) throw new Error(`「${title}」没有渲染出所属卡片`)
  return container
}

describe('仪表盘卡片的取值来源', () => {
  beforeEach(() => {
    mocks.getDashboardStats.mockResolvedValue(SUMMARY)
    mocks.getSystemMetrics.mockResolvedValue(METRICS)
    mocks.getHourlyTrend.mockResolvedValue(TREND)
    mocks.getAggregateStats.mockResolvedValue(WORKER_STATS)
    mocks.getClusterSpiderStats.mockResolvedValue(null)
  })

  it('「完成任务」卡的数值与成功率同取 24 小时窗口', async () => {
    renderDashboard()

    const completed = await card('近24小时完成')

    // 24 小时窗口里成功 9 次、成功率 90.0%。
    expect(within(completed).getByText('9')).toBeInTheDocument()
    expect(within(completed).getByText('成功率 90.0%')).toBeInTheDocument()
    // 全时段的 999 与当日的 42% 都不该再出现在这张卡上。
    expect(within(completed).queryByText('999')).toBeNull()
    expect(within(completed).queryByText('成功率 42.0%')).toBeNull()
    // 标题也不再自称「今日」——窗口是滚动 24 小时，不是自然日。
    expect(screen.queryByText('今日完成任务')).toBeNull()
  })

  it('全部拿到时照常显示真实数值（含真的是 0 的那个）', async () => {
    mocks.getHourlyTrend.mockResolvedValue([{ hour: 0, tasks: 3, success: 3, failed: 0 }])
    renderDashboard()

    expect(within(await card('Worker 状态')).getByText('3 / 4')).toBeInTheDocument()
    expect(within(await card('项目统计')).getByText('5 / 7')).toBeInTheDocument()
    // failed 真的是 0，必须显示 0 而不是占位符。
    expect(within(await card('近24小时异常')).getByText('0')).toBeInTheDocument()
    await waitFor(() => expect(screen.getByText('17')).toBeInTheDocument())
    expect(screen.getByText('63%')).toBeInTheDocument()
  })

  it('部分请求失败时那几块显示占位符，不折算成 0', async () => {
    // 普通用户的现场形状：summary / hourly-trend 可用，metrics 与 workers/stats 403。
    mocks.getSystemMetrics.mockRejectedValue(new Error('403'))
    mocks.getAggregateStats.mockRejectedValue(new Error('403'))
    mocks.getClusterSpiderStats.mockRejectedValue(new Error('403'))

    renderDashboard()

    // 拿到的那一路照常出真值，证明这次渲染确实跑通了、不是整页空白。
    expect(within(await card('项目统计')).getByText('5 / 7')).toBeInTheDocument()

    expect(within(await card('Worker 状态')).getByText('—')).toBeInTheDocument()
    expect(within(await card('Worker 状态')).queryByText('0 / 0')).toBeNull()

    const queueLabel = await screen.findByText('队列中')
    expect(within(queueLabel.parentElement as HTMLElement).getByText('—')).toBeInTheDocument()

    const cpuLabel = await screen.findByText('CPU 使用率')
    expect(within(cpuLabel.parentElement as HTMLElement).getByText('—')).toBeInTheDocument()
    expect(within(cpuLabel.parentElement as HTMLElement).queryByText('0%')).toBeNull()
  })

  it('24 小时趋势没拿到时说没拿到，不画一条 24 个零桶的平线', async () => {
    mocks.getHourlyTrend.mockRejectedValue(new Error('boom'))

    renderDashboard()

    expect(await screen.findByText('24 小时趋势未获取到')).toBeInTheDocument()
    // 同一张卡上的「运行中」来自 summary，仍是真值——证明只有失败的那一块被换掉了。
    const runningLabel = await screen.findByText('运行中')
    expect(within(runningLabel.parentElement as HTMLElement).getByText('4')).toBeInTheDocument()
  })
})
