/**
 * Monitor「关键任务状态」卡片上原有两个交互元素从初版（6f60aca）起就没有 onClick，点下去是
 * 静默空操作——和刚摘掉的任务级 CPU/内存列同型：界面承诺了产品没做的事。
 *
 * - 「详情」：产品其实有任务详情页（App.tsx 的 `tasks/:id` → pages/Tasks/TaskDetail），
 *   MonitorTask.id 就是 TaskResponse.id，与 /tasks 列表页「查看」跳的是同一条路由，接上即可。
 * - 「筛选」：不接。数据源 useTasks 固定取 `page:1,size:20`，本地筛选只作用在这 20 条截断
 *   窗口上，会把"前 20 条里失败的"冒充成"全部失败任务"；真筛选在 /tasks 页走后端。
 *
 * 这里从渲染好的 Monitor 页真实点击，钉住"跳的是被点那一行的 id"和"卡片右上角不再有按钮"。
 */
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter, Route, Routes, useParams } from 'react-router'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import Monitor from './index'

const mocks = vi.hoisted(() => ({
  getTasks: vi.fn(),
  getDashboardStats: vi.fn(),
  getAllWorkers: vi.fn(),
  getAggregateStats: vi.fn(),
  getClusterMetricsHistory: vi.fn(),
  getWorkerMetricsHistory: vi.fn(),
}))

// chart.js 需要 canvas；打桩只是为了让页面能在 jsdom 里渲染，与本用例的按钮行为无关。
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

const TaskDetailProbe = () => {
  const { id } = useParams<{ id: string }>()
  return <div data-testid="task-detail-route">任务详情 {id}</div>
}

const renderMonitor = () =>
  render(
    <MemoryRouter initialEntries={['/dashboard']}>
      <Routes>
        <Route path="/dashboard" element={<Monitor />} />
        <Route path="/tasks/:id" element={<TaskDetailProbe />} />
      </Routes>
    </MemoryRouter>
  )

const cardOf = (title: string | RegExp): HTMLElement => {
  const card = screen.getByText(title).closest('.ant-card')
  if (!card) throw new Error(`未找到标题为 ${String(title)} 的卡片`)
  return card as HTMLElement
}

// antd 会在两个汉字之间插一个空格（autoInsertSpace），"筛选"在 DOM 里其实是"筛 选"——
// 按 name: /筛选/ 查 role=button 会恒返回 null，恰好是一条永远绿的假断言。这里改成把卡片
// 内所有 <button> 的文本去空白后做整份清单全等比对：多出任何一个按钮（有名字的、纯图标的）
// 都会红，且不依赖 antd 的文本插空格细节。
const buttonLabels = (root: HTMLElement): string[] =>
  Array.from(root.querySelectorAll('button')).map((button) => (button.textContent ?? '').replace(/\s+/g, ''))

describe('Monitor 关键任务状态卡片的交互元素都真的做事', () => {
  beforeEach(() => {
    mocks.getTasks.mockResolvedValue({
      items: [
        { id: 'task-1', name: 'data-sync-daily', specified_worker_id: null, status: 'running' },
        { id: 'task-2', name: 'price-crawler', specified_worker_id: null, status: 'failed' },
      ],
      total: 2,
      page: 1,
      size: 20,
    })
    mocks.getDashboardStats.mockResolvedValue({
      tasks: { total: 2, active: 2, running: 1, success: 0, failed: 1 },
    })
    mocks.getAllWorkers.mockResolvedValue([])
    mocks.getAggregateStats.mockResolvedValue({ totalWorkers: 0, onlineWorkers: 0 })
    mocks.getClusterMetricsHistory.mockResolvedValue({
      timestamps: [],
      cpu: { avg: [], max: [], min: [] },
      memory: { avg: [], max: [], min: [] },
    })
    mocks.getWorkerMetricsHistory.mockResolvedValue([])
  })

  it('点第二行的「详情」跳到的是第二行那个任务的详情路由', async () => {
    renderMonitor()

    const detailButtons = await screen.findAllByRole('button', { name: '详情' })
    // 每行一个按钮；只有一个的话下面"点第二行"就退化成"点唯一一行"，钉不住 id 是否写死。
    expect(detailButtons).toHaveLength(2)

    await userEvent.click(detailButtons[1])

    expect(await screen.findByTestId('task-detail-route')).toHaveTextContent('任务详情 task-2')
  })

  it('点第一行的「详情」跳的是 task-1，两行不会跳到同一处', async () => {
    renderMonitor()

    const detailButtons = await screen.findAllByRole('button', { name: '详情' })
    await userEvent.click(detailButtons[0])

    expect(await screen.findByTestId('task-detail-route')).toHaveTextContent('任务详情 task-1')
  })

  it('卡片右上角不再挂空操作的「筛选」按钮', async () => {
    renderMonitor()

    // 先确认表真的渲染出来了，否则"找不到筛选按钮"可能只是整张卡片没渲染。
    await screen.findAllByRole('button', { name: '详情' })

    expect(cardOf('关键任务状态').querySelector('.ant-card-extra')).toBeNull()
    // 对照组：同页 Worker 卡片的右上角确实有 extra（「查看全部」），证明上面这个选择器不是恒空。
    expect(cardOf(/执行 Worker状态/).querySelector('.ant-card-extra')).not.toBeNull()
  })

  it('卡片里的按钮只剩每行一个「详情」和分页器自带的翻页键', async () => {
    renderMonitor()
    await screen.findAllByRole('button', { name: '详情' })

    expect(buttonLabels(cardOf('关键任务状态'))).toEqual(['详情', '详情', '', ''])
  })
})
