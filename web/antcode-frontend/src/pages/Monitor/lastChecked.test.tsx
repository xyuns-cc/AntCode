/**
 * Monitor 页「上次检查」与手动刷新的诚实性。
 *
 * 原实现有三处把失败画成正常：
 *  1. 10 秒轮询的定时器在 `loadWorkers` 之外无条件 `setLastChecked('刚刚')`，
 *     后端挂掉时页面永远显示「刚刚」，且该字符串还被 `createAlerts` 当作每条
 *     告警的时间戳，于是半小时前的陈旧告警也标成「刚刚」。
 *  2. 「N分钟前」是拿正则去解析自己上一次渲染出来的中文文案反推出来的
 *     （值从错误的来源算出来）；又因为 1 每 10 秒重置一次，这条分支实际不可达。
 *  3. `handleRefresh` 不看结果就弹「数据刷新成功」，失败时和拦截器的错误提示同时出现。
 *
 * 判据一正一反：成功必须显示「刚刚」并弹成功提示；失败必须**不**显示「刚刚」、
 * 且**不**弹成功提示。只有正例的话「全好」和「全挂」长得一样。
 */
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import Monitor from './index'
import { describeLastChecked } from './data'

const mocks = vi.hoisted(() => ({
  getTasks: vi.fn(),
  getDashboardStats: vi.fn(),
  getAllWorkers: vi.fn(),
  getAggregateStats: vi.fn(),
  getClusterMetricsHistory: vi.fn(),
  getWorkerMetricsHistory: vi.fn(),
  success: vi.fn(),
  error: vi.fn(),
}))

vi.mock('react-chartjs-2', () => ({
  Bar: () => <div data-testid="bar-chart" />,
  Line: () => <div data-testid="line-chart" />,
}))

vi.mock('@/services/tasks', () => ({ taskService: { getTasks: mocks.getTasks } }))
vi.mock('@/services/dashboard', () => ({
  dashboardService: { getDashboardStats: mocks.getDashboardStats },
}))
vi.mock('@/services/workers', () => ({
  workerService: {
    getAllWorkers: mocks.getAllWorkers,
    getAggregateStats: mocks.getAggregateStats,
    getClusterMetricsHistory: mocks.getClusterMetricsHistory,
    getWorkerMetricsHistory: mocks.getWorkerMetricsHistory,
  },
}))
vi.mock('@/hooks/useMessage', () => ({
  globalMessage: { success: mocks.success, error: mocks.error },
}))

const renderMonitor = () =>
  render(
    <MemoryRouter initialEntries={['/dashboard']}>
      <Monitor />
    </MemoryRouter>
  )

/**
 * 按名字取刷新按钮。
 *
 * 用子串匹配而不是全等：antd 的图标是 `<span role="img" aria-label="sync">`，
 * 会被算进按钮的可访问名（实测为「sync 刷新」，loading 时又变成「loading 刷新」），
 * 所以 `name: '刷新'` 恒不匹配。这是**正向**查询，取不到就直接抛错，不会假绿。
 * （顺带证伪一个猜测：antd 5.29.3 的 `autoInsertSpace` 只加
 * `ant-btn-two-chinese-chars` 类走 letter-spacing，不改 DOM 文本，与此无关。）
 *
 * 按钮 loading 时 antd 会吞掉点击，所以必须等首屏加载结束再点，
 * 否则点了个寂寞、用例会以"没弹提示"的形式假绿。
 */
const clickRefresh = async (): Promise<void> => {
  const button = await screen.findByRole('button', { name: /刷新/ })
  await waitFor(() => expect(button.className).not.toContain('ant-btn-loading'))
  await userEvent.click(button)
}

beforeEach(() => {
  vi.clearAllMocks()
  mocks.getTasks.mockResolvedValue({ items: [], total: 0, page: 1, size: 20 })
  mocks.getDashboardStats.mockResolvedValue({
    tasks: { total: 0, active: 0, running: 0, success: 0, failed: 0 },
  })
  mocks.getClusterMetricsHistory.mockResolvedValue({
    timestamps: [],
    cpu: { avg: [], max: [], min: [] },
    memory: { avg: [], max: [], min: [] },
  })
  mocks.getWorkerMetricsHistory.mockResolvedValue([])
  mocks.getAggregateStats.mockResolvedValue({ totalWorkers: 0, onlineWorkers: 0 })
})

describe('describeLastChecked', () => {
  it('一次都没成功过时不能冒充「刚刚」', () => {
    expect(describeLastChecked(null, 1_000_000)).toBe('尚未成功获取')
  })

  it('按距上次成功的真实间隔变老', () => {
    const base = 1_000_000
    expect(describeLastChecked(base, base)).toBe('刚刚')
    expect(describeLastChecked(base, base + 59_000)).toBe('刚刚')
    expect(describeLastChecked(base, base + 60_000)).toBe('1分钟前')
    expect(describeLastChecked(base, base + 3 * 60_000)).toBe('3分钟前')
    expect(describeLastChecked(base, base + 99 * 60_000)).toBe('10分钟前')
  })
})

describe('Monitor 上次检查时间', () => {
  it('拉取成功时显示「刚刚」', async () => {
    mocks.getAllWorkers.mockResolvedValue([])
    renderMonitor()
    expect(await screen.findByText(/上次检查: 刚刚/)).toBeInTheDocument()
  })

  // 反例：这条在修复前是绿的（页面照样显示「刚刚」），修复后才真正区分开。
  it('拉取失败时不得显示「刚刚」', async () => {
    mocks.getAllWorkers.mockRejectedValue(new Error('worker api down'))
    const { container } = renderMonitor()
    await waitFor(() => expect(mocks.getAllWorkers).toHaveBeenCalled())
    await screen.findByText(/上次检查: 尚未成功获取/)
    expect(container.textContent).not.toContain('上次检查: 刚刚')
  })
})

describe('Monitor 手动刷新', () => {
  it('刷新成功才弹成功提示', async () => {
    mocks.getAllWorkers.mockResolvedValue([])
    renderMonitor()
    await waitFor(() => expect(mocks.getAllWorkers).toHaveBeenCalled())

    await clickRefresh()
    await waitFor(() => expect(mocks.success).toHaveBeenCalledWith('数据刷新成功'))
  })

  // 反例：原实现不看结果就报成功，会和错误提示同时弹出来。
  it('刷新失败时只报错、不得报成功', async () => {
    mocks.getAllWorkers.mockRejectedValue(new Error('worker api down'))
    renderMonitor()
    await waitFor(() => expect(mocks.getAllWorkers).toHaveBeenCalled())

    await clickRefresh()
    await waitFor(() => expect(mocks.error).toHaveBeenCalledWith('加载Worker 数据失败'))
    expect(mocks.success).not.toHaveBeenCalled()
  })

})
