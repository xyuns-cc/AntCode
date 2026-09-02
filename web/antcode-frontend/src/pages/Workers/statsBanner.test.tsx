/**
 * Worker 页顶部「平均 CPU / 平均内存」曾对**所有** Worker 求平均，没上报过指标的机器被
 * 当成 0 计入分母：4 台里 3 台没心跳时，12% 的真实占用被报成 3%——越是机器刚上线、越是
 * 心跳断了的时候，这块面板越显得健康。
 *
 * 后端 `worker_stats_service.get_aggregate_stats` 的分母是 `workers_with_metrics`，这里
 * 钉住前端与它同口径，并且一台都没上报时显示占位符而不是「0%」。
 *
 * 判据成对：正例钉真值 12%，反例钉「不是 3%」——只断言 12 的话，一个恒显示 12 的实现
 * 也能过；只断言"不是 3"的话，显示 '—' 也能过。
 */
import { render } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import type { Worker } from '@/types'
import Workers from './index'

const mocks = vi.hoisted(() => ({ workers: [] as Worker[] }))

vi.mock('@/stores/workerStore', () => ({
  useWorkerStore: () => ({
    workers: mocks.workers,
    currentWorker: undefined,
    loading: false,
    refreshWorkers: vi.fn(),
    silentRefresh: vi.fn(),
    setCurrentWorker: vi.fn(),
    removeWorker: vi.fn(),
    updateWorker: vi.fn(),
    lastRefreshed: 0,
  }),
}))

vi.mock('@/services/workers', () => ({ workerService: {} }))
vi.mock('@/services/users', () => ({ userService: {} }))

const worker = (id: string, metrics: Worker['metrics']): Worker => ({
  id,
  name: `node-${id}`,
  host: '192.168.1.250',
  port: 8080,
  status: 'online',
  metrics,
  createdAt: '2026-01-01T00:00:00Z',
})

const fullMetrics = (cpu: number, memory: number) => ({
  cpu,
  memory,
  disk: 0,
  taskCount: 0,
  runningTasks: 0,
  projectCount: 0,
  envCount: 0,
  uptime: 0,
})

// antd Statistic 把数值与 suffix 拆成相邻节点；按标题定位到那一张卡再读整块内容文本，
// 避免拿整页文本做子串匹配时被表格里的其它数字蒙混过关。
const statisticValue = (container: HTMLElement, title: string): string => {
  const titleNode = Array.from(container.querySelectorAll('.ant-statistic-title'))
    .find((node) => node.textContent === title)
  if (!titleNode) throw new Error(`没有渲染出「${title}」这张卡`)
  return titleNode.parentElement?.querySelector('.ant-statistic-content')?.textContent?.trim() ?? ''
}

describe('Worker 页平均使用率不被「没有指标的机器」稀释', () => {
  it('只有上报过指标的机器进分母', () => {
    mocks.workers = [
      worker('1', fullMetrics(12, 40)),
      worker('2', null),
      worker('3', null),
      worker('4', undefined),
    ]

    const { container } = render(<Workers />)

    // 旧实现按 workers.length 除：12/4=3、40/4=10。全等断言同时钉死正值与那两个错值。
    expect(statisticValue(container, '平均 CPU')).toBe('12%')
    expect(statisticValue(container, '平均内存')).toBe('40%')
  })

  it('一台都没上报过时显示占位符而不是 0%', () => {
    mocks.workers = [worker('1', null), worker('2', null)]

    const { container } = render(<Workers />)

    expect(statisticValue(container, '平均 CPU')).toBe('—')
    expect(statisticValue(container, '平均内存')).toBe('—')
    // 同一张 banner 上「离线 0」照常显示 0：占位符换掉的只是算不出来的均值。
    expect(statisticValue(container, '离线')).toBe('0')
  })
})
