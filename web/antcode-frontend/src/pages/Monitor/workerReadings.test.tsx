/**
 * 「从没上报过指标」不许被画成「占用 0%」。
 *
 * transformWorker 把缺指标的机器压成 cpu/memory/disk = 0（阈值判定和条形图都要数字），
 * 集群均值和磁盘图早就靠 hasMetrics 把它们排除掉了，但 Worker 卡片 / 全部 Worker 抽屉 /
 * Worker 详情三处仍在照单全画：一台一次心跳都没有的机器被画成三根 0% 的彩色进度条。
 *
 * 判据成对，且**从后端回包形状出发**走一遍 transformWorker：本仓抓到过「只喂手搓
 * WorkerDisplayData」的假绿——transformWorker 把 hasMetrics 写死 true 时那些用例照样全绿。
 * 正例钉「真的上报了 0% 必须显示 0% 并照常涂色」，反例钉「没上报过必须是占位符且不涂色」。
 */
import { render, screen, within } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import type { Worker, WorkerMetrics } from '@/types'
import { createWorkerDetailData } from './charts/data'
import { transformWorker } from './data'
import { WorkerOverview } from './drawers/WorkerOverview'
import { WorkerTrendCard } from './drawers/WorkerTrendCard'
import { WorkersDrawer } from './drawers/WorkersDrawer'
import { WorkersSection } from './sections/WorkersSection'

vi.mock('react-chartjs-2', () => ({ Line: () => <div data-testid="line-chart" /> }))

const NEUTRAL_BAR = '#d9d9d9'
const CPU_BAR = '#1890ff'

const apiWorker = (name: string, metrics: WorkerMetrics | null): Worker => ({
  id: `id-${name}`,
  name,
  host: '192.168.1.250',
  port: 8080,
  status: 'online',
  createdAt: '2026-09-01T00:00:00Z',
  metrics,
})

const idleMetrics: WorkerMetrics = {
  cpu: 0, memory: 0, disk: 0, taskCount: 0, runningTasks: 0, projectCount: 0, envCount: 0, uptime: 60,
}

const reported = transformWorker(apiWorker('idle-node', idleMetrics))
const silent = transformWorker(apiWorker('silent-node', null))

// antd 把 strokeColor 写进 .ant-progress-bg 的行内样式。取整块卡片里的第一根条
// （CPU），够用来区分「涂了业务色」和「中性灰」。
const firstBarStyle = (card: HTMLElement): string =>
  card.querySelector('.ant-progress-bg')?.getAttribute('style') ?? ''

const cardOf = (container: HTMLElement, name: string): HTMLElement => {
  const heading = within(container).getByText(name)
  const card = heading.closest('.ant-card')
  if (!card) throw new Error(`没有渲染出 ${name} 的卡片`)
  return card as HTMLElement
}

const renderSection = (workers: typeof reported[]) =>
  render(
    <WorkersSection
      workers={workers}
      loading={false}
      lastChecked="刚刚"
      onShowAll={vi.fn()}
      onSelect={vi.fn()}
    />
  )

const renderDrawer = (workers: typeof reported[]) =>
  render(<WorkersDrawer open workers={workers} onClose={vi.fn()} onSelect={vi.fn()} />)

describe('Worker 卡片的 CPU / 内存读数', () => {
  it('真的上报了 0% 的机器显示 0% 并照常涂业务色', () => {
    const { container } = renderSection([reported])
    const card = cardOf(container, 'idle-node')

    expect(within(card).getAllByText('0%').length).toBeGreaterThan(0)
    expect(within(card).queryByText('—')).toBeNull()
    expect(firstBarStyle(card)).toContain(CPU_BAR)
  })

  it('从没上报过的机器显示占位符，且条形不涂色', () => {
    const { container } = renderSection([silent])
    const card = cardOf(container, 'silent-node')

    // 旧实现：两行都是 0%，配一根蓝色 / 绿色的条 —— 与上面那台真的很闲的机器同形。
    expect(within(card).getAllByText('—')).toHaveLength(2)
    expect(within(card).queryByText('0%')).toBeNull()
    expect(firstBarStyle(card)).toContain(NEUTRAL_BAR)
  })
})

describe('全部 Worker 抽屉里的卡片', () => {
  it('真的上报了 0% 的机器照常显示 0%', () => {
    const { container } = renderDrawer([reported])
    const card = cardOf(container.ownerDocument.body, 'idle-node')

    expect(within(card).getAllByText('0%').length).toBeGreaterThan(0)
    expect(within(card).queryByText('—')).toBeNull()
  })

  it('从没上报过的机器显示占位符，且条形不涂色', () => {
    const { container } = renderDrawer([silent])
    const card = cardOf(container.ownerDocument.body, 'silent-node')

    expect(within(card).getAllByText('—')).toHaveLength(2)
    expect(within(card).queryByText('0%')).toBeNull()
    expect(firstBarStyle(card)).toContain(NEUTRAL_BAR)
  })
})

describe('Worker 详情的三条资源进度', () => {
  it('真的上报了 0% 时三条都写 0%', () => {
    render(<WorkerOverview worker={reported} />)

    expect(screen.getAllByText('0%')).toHaveLength(3)
    expect(screen.queryByText('—')).toBeNull()
  })

  it('从没上报过时三条都是占位符', () => {
    const { container } = render(<WorkerOverview worker={silent} />)

    // 旧实现：CPU / 内存 / 磁盘三条都写 0%，与真的空闲无法区分。
    expect(screen.getAllByText('—')).toHaveLength(3)
    expect(screen.queryByText('0%')).toBeNull()
    expect(firstBarStyle(container)).toContain(NEUTRAL_BAR)
  })
})

describe('Worker 详情的资源趋势图', () => {
  it('上报过读数时照常出图', () => {
    render(
      <WorkerTrendCard
        chartRef={{ current: null }}
        data={createWorkerDetailData(reported, [])}
        options={{}}
      />
    )

    expect(screen.getByTestId('line-chart')).toBeInTheDocument()
    expect(screen.queryByText('该 Worker 尚未上报过资源指标')).toBeNull()
  })

  it('既没历史也没上报过时写明原因，而不是留一张空白画布', () => {
    render(
      <WorkerTrendCard
        chartRef={{ current: null }}
        data={createWorkerDetailData(silent, [])}
        options={{}}
      />
    )

    // 旧实现：画一个标着「当前」的点，CPU 0% / 内存 0%。
    expect(screen.getByText('该 Worker 尚未上报过资源指标')).toBeInTheDocument()
    expect(screen.queryByTestId('line-chart')).toBeNull()
  })
})
