/**
 * 爬虫统计页的两路请求各自成败。
 *
 * loadData 曾把 /workers/stats/spider 和 /workers 塞进一个 Promise.all 再整块 catch：任一路
 * 失败，指标卡和 Worker 下拉一起空掉 —— 「统计接口挂了」和「集群里一台 Worker 都没有」在
 * 界面上长得一模一样，而且失败那一轮还会往趋势图里补一个 0 采样，画出一次并不存在的跌零。
 *
 * 判据成对：每条负例都同时钉住「另一路仍然拿到了真值」，否则一个「两路都返回 null」的实现
 * 也能全过；正例钉住两路都成功时值照常出来。
 */
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import type { ClusterSpiderStats, Worker } from '@/types'
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

const STATS: ClusterSpiderStats = {
  totalRequests: 1234,
  totalResponses: 1200,
  totalItemsScraped: 77,
  totalErrors: 9,
  avgLatencyMs: 306,
  domainStats: [],
  clusterRequestsPerMinute: 42,
  workerCount: 1,
  statusCodes: { '200': 1200 },
}

const ONLINE_WORKER: Worker = {
  id: 'w-1',
  name: 'node-a',
  host: '192.168.1.250',
  port: 8080,
  status: 'online',
  createdAt: '2026-09-01T00:00:00Z',
}

const metricValue = async (title: string): Promise<string> => {
  const titleNode = await screen.findByText(title)
  const valueRow = titleNode.parentElement?.children[1]
  if (!valueRow) throw new Error(`「${title}」没有渲染出数值行`)
  return (valueRow.textContent ?? '').trim()
}

const detailValue = async (label: string): Promise<string> => {
  const labelNode = await screen.findByText(label)
  const row = labelNode.parentElement
  if (!row) throw new Error(`「${label}」没有渲染出所属行`)
  return (row.textContent ?? '').replace(label, '').trim()
}

// 页面上只有 Worker 那个下拉是空值带 placeholder 的（时长下拉恒有 value）。
const workerSelect = async (): Promise<HTMLElement> => {
  const placeholder = await screen.findByText(/^(选择 Worker|Worker 列表未取到)$/)
  const select = placeholder.closest('.ant-select')
  if (!select) throw new Error('没有渲染出 Worker 下拉')
  return select as HTMLElement
}

const cardOf = async (title: string): Promise<HTMLElement> => {
  const titleNode = await screen.findByText(title)
  const card = titleNode.closest('.ant-card')
  if (!card) throw new Error(`没有渲染出「${title}」卡片`)
  return card as HTMLElement
}

describe('爬虫统计页的统计与 Worker 列表各自成败', () => {
  it('两路都成功时指标卡、Worker 下拉、趋势图都是真值', async () => {
    mocks.getClusterSpiderStats.mockResolvedValue(STATS)
    mocks.getAllWorkers.mockResolvedValue([ONLINE_WORKER])

    render(<SpiderStatsTab />)

    expect(await metricValue('异常 & 错误')).toBe('9')
    expect(await detailValue('请求总数')).toBe('1,234')
    const select = await workerSelect()
    expect(within(select).getByText('选择 Worker')).toBeInTheDocument()
    expect(within(await cardOf('最近完成请求量趋势')).getByTestId('line-chart')).toBeInTheDocument()
  })

  it('统计挂了时卡片是占位符，Worker 下拉照常可选', async () => {
    mocks.getClusterSpiderStats.mockRejectedValue(new Error('503'))
    mocks.getAllWorkers.mockResolvedValue([ONLINE_WORKER])

    render(<SpiderStatsTab />)

    expect(await metricValue('异常 & 错误')).toBe('—')
    // 旧实现这里是 '0'：一次 503 被渲染成「一条请求都没发过」。
    expect(await detailValue('请求总数')).toBe('—')

    // 另一路仍然拿到了真值 —— 排除「两路都被清空」造成的假绿。
    const select = await workerSelect()
    expect(within(select).getByText('选择 Worker')).toBeInTheDocument()
    expect(select.className).not.toContain('ant-select-disabled')
    fireEvent.mouseDown(select.querySelector('.ant-select-selector')!)
    await waitFor(() => expect(screen.getByTitle('node-a')).toBeInTheDocument())
  })

  it('统计挂了的那一轮不往趋势图补采样点', async () => {
    mocks.getClusterSpiderStats.mockRejectedValue(new Error('503'))
    mocks.getAllWorkers.mockResolvedValue([ONLINE_WORKER])

    render(<SpiderStatsTab />)

    // 旧实现补一个 reqRate=0 的点，趋势线上出现一次并不存在的跌零。
    const card = await cardOf('最近完成请求量趋势')
    await waitFor(() => expect(within(card).getByText('暂无数据')).toBeInTheDocument())
    expect(within(card).queryByTestId('line-chart')).toBeNull()
  })

  it('Worker 列表挂了时下拉写明没取到，指标卡照常是真值', async () => {
    mocks.getClusterSpiderStats.mockResolvedValue(STATS)
    mocks.getAllWorkers.mockRejectedValue(new Error('503'))

    render(<SpiderStatsTab />)

    // 旧实现下拉是一个空选项列表，与「一台在线 Worker 都没有」无法区分。
    const select = await workerSelect()
    expect(within(select).getByText('Worker 列表未取到')).toBeInTheDocument()
    expect(select.className).toContain('ant-select-disabled')

    expect(await metricValue('异常 & 错误')).toBe('9')
    expect(await detailValue('请求总数')).toBe('1,234')
  })
})
