/**
 * Monitor 的两张任务表曾并排挂着"CPU""内存"两列，但它们的值不是"还没取到"，而是 mock 版
 * 改真接口时留下来的字面量 '-'：后端 TaskResponse 没有任何任务级资源字段，Worker 侧采到的
 * 用量在 engine.py::_task_result 组装上报时就被丢掉，task_executions 的同名列也已因恒 NULL
 * 被摘（e7b7249）。这些用例钉住两处表头不再出现这两列、且 useTasks 不再往行数据里塞占位符
 * ——加回来就等于又把"产品没有这个数据"伪装成"数据还没加载到"，和 last_run_duration 同型。
 */
import { render, renderHook, screen, waitFor } from '@testing-library/react'
import type { ChartData, ChartOptions } from 'chart.js'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { WorkerTasksCard } from './drawers/WorkerTasksCard'
import { useTasks } from './hooks/useTasks'
import { TasksSection } from './sections/TasksSection'
import type { MonitorTask, WorkerDisplayData } from './types'

const mocks = vi.hoisted(() => ({ getTasks: vi.fn(), getDashboardStats: vi.fn() }))

// 图表与本用例无关，且 chart.js 需要 canvas；打桩掉只是为了让表格能在 jsdom 里渲染出来。
vi.mock('react-chartjs-2', () => ({ Bar: () => <div data-testid="bar-chart" /> }))

vi.mock('@/services/tasks', () => ({ taskService: { getTasks: mocks.getTasks } }))

vi.mock('@/services/dashboard', () => ({
  dashboardService: { getDashboardStats: mocks.getDashboardStats },
}))

// 开了 scroll.x 的 antd 表会把表头渲染两遍（吸顶表 + 量宽用的隐藏表），按 DOM 顺序去重后
// 才是真正的列清单。整列清单做全等断言，比逐个查"CPU 在不在"更难被将来的改动绕过。
const headerTitles = (container: HTMLElement): string[] => {
  const titles = Array.from(container.querySelectorAll('.ant-table-thead th'))
    .map((cell) => cell.textContent?.trim() ?? '')
    .filter(Boolean)
  return Array.from(new Set(titles))
}

const task: MonitorTask = {
  id: 'task-1',
  name: 'data-sync-daily',
  worker: 'node-01',
  status: 'running',
}

const worker: WorkerDisplayData = {
  id: 'worker-1',
  name: 'node-01',
  version: 'v1.3.0',
  os: 'ubuntu',
  status: 'running',
  cpu: 12,
  memory: 34,
  disk: 56,
  tasks: 2,
  uptime: '3天',
  host: '192.168.1.250',
  port: 8080,
}

const emptyChart: ChartData<'bar'> = { labels: [], datasets: [] }
const emptyOptions: ChartOptions<'bar'> = {}

describe('Monitor 任务表不展示任务级 CPU/内存', () => {
  beforeEach(() => {
    mocks.getTasks.mockResolvedValue({
      items: [{ id: 'task-1', name: 'data-sync-daily', specified_worker_id: 'worker-1', status: 'running' }],
      total: 1,
      page: 1,
      size: 20,
    })
    mocks.getDashboardStats.mockResolvedValue({
      tasks: { total: 1, active: 1, running: 1, success: 0, failed: 0 },
    })
  })

  it('「关键任务状态」表只保留后端真有数据的列', () => {
    const { container } = render(
      <TasksSection
        tasks={[task]}
        onViewTask={vi.fn()}
        taskStatsData={emptyChart}
        diskUsageData={emptyChart}
        taskBarOptions={emptyOptions}
        diskBarOptions={emptyOptions}
      />
    )

    expect(headerTitles(container)).toEqual(['序号', '任务名称', '执行 Worker', '状态', '操作'])
    // 行数据确实渲染到了这些列上，否则上面的列清单可能只是一张空壳表。
    expect(screen.getByText('data-sync-daily')).toBeInTheDocument()
    expect(screen.getByText('node-01')).toBeInTheDocument()
  })

  it('Worker 抽屉「运行任务列表」表只保留后端真有数据的列', () => {
    const { container } = render(<WorkerTasksCard tasks={[task]} />)

    expect(headerTitles(container)).toEqual(['序号', '任务名称', '状态'])
    expect(screen.getByText('data-sync-daily')).toBeInTheDocument()
  })

  it('useTasks 映射出的行数据里没有 cpu/memory 占位符', async () => {
    const { result } = renderHook(() => useTasks([worker]))

    await waitFor(() => expect(result.current.tasks).toHaveLength(1))

    const [mapped] = result.current.tasks
    // 正向确认映射本身是通的，避免"字段不存在"只是因为整条链路没跑起来。
    expect(mapped).toMatchObject({ id: 'task-1', name: 'data-sync-daily', worker: 'node-01', status: 'running' })
    expect(mapped).not.toHaveProperty('cpu')
    expect(mapped).not.toHaveProperty('memory')
  })
})
