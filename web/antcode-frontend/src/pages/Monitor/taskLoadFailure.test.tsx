/**
 * 监控页任务区的「没取到」必须看得见。
 *
 * useTasks 过去把两路请求塞进 Promise.all，再整块 catch 掉：任一路失败，tasks 保持 []、
 * counts 保持全 0，界面上除了一行 console.error 之外没有任何信号——「后端挂了」和「今天
 * 一个任务都没有」长得一模一样，刷新一次还会把上一轮的行数据继续摆在那儿当现况。
 *
 * 判据成对：每条负例都同时钉住「另一路仍然拿到了真值」，否则一个恒返回 null 的实现也能
 * 全过；正例钉住两路都成功时值照常出来。
 */
import { render, renderHook, screen, waitFor } from '@testing-library/react'
import type { ChartData, ChartOptions } from 'chart.js'
import { MemoryRouter } from 'react-router'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import Monitor from './index'
import { WorkerTasksCard } from './drawers/WorkerTasksCard'
import { useTasks } from './hooks/useTasks'
import { TasksSection } from './sections/TasksSection'
import type { MonitorTask } from './types'

const mocks = vi.hoisted(() => ({
  getTasks: vi.fn(),
  getDashboardStats: vi.fn(),
  getAllWorkers: vi.fn(),
  getAggregateStats: vi.fn(),
  getClusterMetricsHistory: vi.fn(),
  getWorkerMetricsHistory: vi.fn(),
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

const taskPage = {
  items: [
    { id: 't-1', name: 'alpha', status: 'running', execution_strategy: 'specified', specified_worker_name: 'node-01' },
    { id: 't-2', name: 'beta', status: 'success', execution_strategy: 'auto' },
  ],
  total: 2,
  page: 1,
  size: 20,
}

const summary = { tasks: { total: 10, active: 4, running: 4, success: 5, failed: 1 } }

describe('useTasks 两路请求各自成败', () => {
  beforeEach(() => {
    mocks.getTasks.mockResolvedValue(taskPage)
    mocks.getDashboardStats.mockResolvedValue(summary)
  })

  it('两路都成功时列表和汇总都是真值', async () => {
    const { result } = renderHook(() => useTasks(1))

    await waitFor(() => expect(result.current.tasks).toHaveLength(2))
    expect(result.current.counts).toEqual({ success: 5, failed: 1, running: 4, pending: 0 })
  })

  it('列表挂了是 null 而不是空表，同一轮的汇总照常拿到', async () => {
    mocks.getTasks.mockRejectedValue(new Error('500'))

    const { result } = renderHook(() => useTasks(1))

    // 汇总拿到真值 = 这一轮确实跑完了，排除「什么都没执行」造成的假绿。
    await waitFor(() => expect(result.current.counts).toEqual({ success: 5, failed: 1, running: 4, pending: 0 }))
    expect(result.current.tasks).toBeNull()
    expect(result.current.tasks).not.toEqual([])
  })

  it('汇总挂了是 null 而不是全 0，同一轮的列表照常拿到', async () => {
    mocks.getDashboardStats.mockRejectedValue(new Error('403'))

    const { result } = renderHook(() => useTasks(1))

    await waitFor(() => expect(result.current.tasks).toHaveLength(2))
    expect(result.current.counts).toBeNull()
    expect(result.current.counts).not.toEqual({ success: 0, failed: 0, running: 0, pending: 0 })
  })

  it('刷新失败时清掉上一轮的行，不把陈旧数据当现况摆着', async () => {
    const { result, rerender } = renderHook(({ signal }) => useTasks(signal), {
      initialProps: { signal: 1 },
    })

    await waitFor(() => expect(result.current.tasks).toHaveLength(2))

    mocks.getTasks.mockRejectedValue(new Error('500'))
    rerender({ signal: 2 })

    await waitFor(() => expect(result.current.tasks).toBeNull())
  })
})

const emptyChart: ChartData<'bar'> = { labels: [], datasets: [] }
const emptyOptions: ChartOptions<'bar'> = {}

const renderTasksSection = (tasks: MonitorTask[] | null) =>
  render(
    <TasksSection
      tasks={tasks}
      onViewTask={vi.fn()}
      taskStatsData={emptyChart}
      diskUsageData={emptyChart}
      taskBarOptions={emptyOptions}
      diskBarOptions={emptyOptions}
    />
  )

describe('任务表把「没取到」和「真的没有」显示成两回事', () => {
  it('后端说真的没有任务时是「暂无数据」', () => {
    renderTasksSection([])

    expect(screen.getByText('暂无数据')).toBeInTheDocument()
    expect(screen.queryByText('任务列表加载失败')).not.toBeInTheDocument()
  })

  it('这一路没取到时表格上写明加载失败', () => {
    renderTasksSection(null)

    expect(screen.getByText('任务列表加载失败')).toBeInTheDocument()
    expect(screen.queryByText('暂无数据')).not.toBeInTheDocument()
  })

  it('Worker 抽屉里的任务卡同样分得开', () => {
    const { unmount } = render(<WorkerTasksCard tasks={[]} />)
    expect(screen.getByText('暂无数据')).toBeInTheDocument()
    unmount()

    render(<WorkerTasksCard tasks={null} />)
    expect(screen.getByText('任务列表加载失败')).toBeInTheDocument()
  })
})

/**
 * 抽屉里这张卡的过滤依据是 MonitorTask.worker——describeTaskWorkerBinding 给出的**绑定**
 * 文案，不是「这一次实际跑在哪台」（后端拿不到，见 utils/taskWorkerBinding）。而且数据源
 * 只有任务列表前 20 条。标题和副标题必须把这两件事都说出来，否则就是拿绑定冒充执行、
 * 拿截断窗口冒充全集。
 */
describe('Worker 抽屉任务卡的措辞不冒充「执行」也不冒充「全集」', () => {
  it('标题说绑定，并写明数据源只有前 20 条', () => {
    render(<WorkerTasksCard tasks={[]} />)

    expect(screen.getByText('绑定到该 Worker 的任务')).toBeInTheDocument()
    expect(screen.getByText('仅取自任务列表前 20 条')).toBeInTheDocument()
    expect(screen.queryByText('运行任务列表')).not.toBeInTheDocument()
  })
})

/**
 * 钉整条链路，不只是钉组件入参。上面那几条都是直接给 TasksSection 传 `null`——只要
 * Monitor/index.tsx 在中间补一句 `tasks={monitor.tasks ?? []}`，界面就退回静默空表，
 * 而它们照样全绿。这条从真实的 service 失败出发，一路到页面上看得见的字。
 */
describe('监控页整页渲染时任务列表失败是看得见的', () => {
  beforeEach(() => {
    mocks.getAllWorkers.mockResolvedValue([])
    mocks.getAggregateStats.mockResolvedValue({ totalWorkers: 0, onlineWorkers: 0 })
    mocks.getClusterMetricsHistory.mockResolvedValue({
      timestamps: [],
      cpu: { avg: [], max: [], min: [] },
      memory: { avg: [], max: [], min: [] },
    })
    mocks.getWorkerMetricsHistory.mockResolvedValue([])
    mocks.getDashboardStats.mockResolvedValue(summary)
  })

  it('/tasks 挂了时页面上出现「任务列表加载失败」', async () => {
    mocks.getTasks.mockRejectedValue(new Error('500'))

    render(<MemoryRouter><Monitor /></MemoryRouter>)

    expect(await screen.findByText('任务列表加载失败')).toBeInTheDocument()
  })

  it('/tasks 正常时页面上是任务行，不是失败文案', async () => {
    mocks.getTasks.mockResolvedValue(taskPage)

    render(<MemoryRouter><Monitor /></MemoryRouter>)

    expect(await screen.findByText('alpha')).toBeInTheDocument()
    expect(screen.queryByText('任务列表加载失败')).not.toBeInTheDocument()
  })
})
