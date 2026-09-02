import { describe, expect, it } from 'vitest'

import { calculateClusterMetrics, createDiskUsageData, createTaskStatsData, createTrendData } from './data'
import { transformWorker } from '../data'
import type { ClusterHistory, WorkerDisplayData } from '../types'
import type { Worker, WorkerMetrics } from '@/types'

const displayWorker = (overrides: Partial<WorkerDisplayData>): WorkerDisplayData => ({
  id: 'w-1',
  name: 'node-1',
  version: 'v1.0.0',
  os: 'linux',
  status: 'running',
  hasMetrics: true,
  cpu: 0,
  memory: 0,
  disk: 0,
  tasks: 0,
  uptime: '1小时 0分钟',
  host: '127.0.0.1',
  port: 8000,
  ...overrides,
})

// 监控页的 transformWorker 把缺指标的机器压成 cpu=0，集群统计过去照单全收：4 台里
// 3 台没心跳时 12% 被除成 3%，集群最小 CPU 还被永久钉在 0。分母口径见 metricAverage，
// 与后端 worker_stats_service.get_aggregate_stats 一致。
describe('calculateClusterMetrics 把没有指标的机器排除在分母和极值之外', () => {
  it('只统计上报过指标的机器', () => {
    const metrics = calculateClusterMetrics([
      displayWorker({ id: 'a', hasMetrics: true, cpu: 12, memory: 40 }),
      displayWorker({ id: 'b', hasMetrics: false, cpu: 0, memory: 0 }),
      displayWorker({ id: 'c', hasMetrics: false, cpu: 0, memory: 0 }),
      displayWorker({ id: 'd', hasMetrics: false, cpu: 0, memory: 0 }),
    ])

    // 旧实现：avgCpu 12/4=3、minCpu=0。全等断言同时钉正值与错值。
    expect(metrics).toEqual({
      avgCpu: 12, avgMem: 40, maxCpu: 12, maxMem: 40, minCpu: 12, minMem: 40,
    })
  })

  it('真的上报了 0% 的机器照常参与', () => {
    const metrics = calculateClusterMetrics([
      displayWorker({ id: 'a', hasMetrics: true, cpu: 12, memory: 40 }),
      displayWorker({ id: 'b', hasMetrics: true, cpu: 0, memory: 0 }),
    ])

    expect(metrics.avgCpu).toBe(6)
    expect(metrics.minCpu).toBe(0)
  })

  it('一台都没上报过时是 null，不是 0', () => {
    const metrics = calculateClusterMetrics([displayWorker({ hasMetrics: false })])

    expect(metrics).toEqual({
      avgCpu: null, avgMem: null, maxCpu: null, maxMem: null, minCpu: null, minMem: null,
    })
  })
})

describe('createTaskStatsData', () => {
  it('uses full server aggregates instead of the preview task page', () => {
    const chart = createTaskStatsData({ success: 120, failed: 30, running: 25, pending: 15 })

    expect(chart.datasets[0].data).toEqual([120, 30, 25, 15])
  })

  // useTasks 拿不到 /dashboard/summary 时给 null。四根 0 高的彩色柱子和「真的一个任务
  // 都没有」长得一模一样，那就是把这一路的失败画成了正常。
  it('汇总真的全是 0 时照常画那四根柱子', () => {
    const chart = createTaskStatsData({ success: 0, failed: 0, running: 0, pending: 0 })

    expect(chart.labels).toEqual(['成功', '失败', '运行中', '待执行'])
    expect(chart.datasets[0].data).toEqual([0, 0, 0, 0])
  })

  it('汇总没取到时不画那四根柱子', () => {
    const chart = createTaskStatsData(null)

    expect(chart.labels).toEqual(['任务统计未取到'])
    expect(chart.datasets[0].data).toEqual([0])
  })
})

/**
 * 磁盘条要和集群均值走同一道过滤：transformWorker 把没上报过指标的机器压成 disk=0，
 * 照单全画就会给一台从没上报过的机器挂一根写着它名字的「0% 磁盘」条——和真的空盘无法
 * 区分。上一轮给 CPU/内存补了 hasMetrics，磁盘这条漏了。
 *
 * 判据成对：真的上报了 0% 必须画出来，从没上报过必须一根都不画。
 */
describe('createDiskUsageData 只画上报过指标的机器', () => {
  it('真的上报了 0% 的机器照常出现在图上', () => {
    const chart = createDiskUsageData([
      displayWorker({ id: 'a', name: 'node-a', hasMetrics: true, disk: 0 }),
      displayWorker({ id: 'b', name: 'node-b', hasMetrics: true, disk: 91 }),
    ])

    expect(chart.labels).toEqual(['node-a', 'node-b'])
    expect(chart.datasets[0].data).toEqual([0, 91])
  })

  it('从没上报过的机器既不占标签，也不贡献一根 0% 条', () => {
    const chart = createDiskUsageData([
      displayWorker({ id: 'a', name: 'node-a', hasMetrics: true, disk: 91 }),
      displayWorker({ id: 'b', name: 'silent', hasMetrics: false, disk: 0 }),
    ])

    // 旧实现：labels=['node-a','silent']、data=[91,0]。两条全等断言同时钉正值与错值。
    expect(chart.labels).toEqual(['node-a'])
    expect(chart.datasets[0].data).toEqual([91])
  })

  it('有机器但一台都没上报过时，占位说的是没上报，不是「暂无数据」', () => {
    const chart = createDiskUsageData([displayWorker({ name: 'silent', hasMetrics: false })])

    expect(chart.labels).toEqual(['无机器上报磁盘用量'])
  })
})

const apiWorker = (name: string, metrics: WorkerMetrics | null): Worker => ({
  id: `id-${name}`,
  name,
  host: '192.168.1.250',
  port: 8080,
  status: 'online',
  createdAt: '2026-09-01T00:00:00Z',
  metrics,
})

const reportedMetrics = (disk: number): WorkerMetrics => ({
  cpu: 5, memory: 6, disk, taskCount: 0, runningTasks: 0, projectCount: 0, envCount: 0, uptime: 60,
})

/**
 * 钉的是链路，不是手搓 fixture。本仓抓到过这种假绿：transformWorker 把 hasMetrics 写死
 * true，集群指标那几条用例照样全绿——因为判据只喂了自己造的 WorkerDisplayData，从没让
 * 后端形状走一遍映射。这条从「后端回了 metrics: null」出发，写死 true 就会红。
 */
describe('磁盘图的过滤依据来自 transformWorker 的映射结果', () => {
  it('后端回 metrics=null 的机器，经 transformWorker 后不进磁盘图', () => {
    const chart = createDiskUsageData([
      transformWorker(apiWorker('node-a', reportedMetrics(91))),
      transformWorker(apiWorker('silent', null)),
    ])

    expect(chart.labels).toEqual(['node-a'])
    expect(chart.datasets[0].data).toEqual([91])
  })

  it('后端回 disk=0 的机器，经 transformWorker 后照常进图', () => {
    const chart = createDiskUsageData([transformWorker(apiWorker('node-a', reportedMetrics(0)))])

    expect(chart.labels).toEqual(['node-a'])
    expect(chart.datasets[0].data).toEqual([0])
  })
})

const clusterMetrics = { avgCpu: 0, avgMem: 0, maxCpu: 0, maxMem: 0, minCpu: 0, minMem: 0 }

const history = (points: number): ClusterHistory => ({
  timestamps: Array.from({ length: points }, (_, index) => `2026-08-${10 + index}`),
  cpu: {
    avg: Array.from({ length: points }, () => 30.1),
    max: Array.from({ length: points }, () => 82.9),
    min: Array.from({ length: points }, () => 0),
  },
  memory: {
    avg: Array.from({ length: points }, () => 10.9),
    max: Array.from({ length: points }, () => 17.5),
    min: Array.from({ length: points }, () => 0),
  },
})

// 回归：30d 粒度按天聚合，集群跑了不到两天时 /workers/cluster/metrics/history
// 只回一个时间点。折线的 pointRadius 曾恒为 0，单点没有线段可画，整张图看起来
// 完全空白 —— 和"接口没数据"无法区分，而接口其实给了 avg 30.1。
// 平均 / 最大 / 最小三条线。先钉住条数再进 forEach：datasets 为空时 forEach 一条
// 断言都不执行，"图被掏空"和"图正常"会长得一模一样——正是这两条用例要防的那种空白。
const TREND_DATASET_COUNT = 3

describe('createTrendData keeps a single history point visible', () => {
  it('gives every dataset a drawable point radius when there is one sample', () => {
    const chart = createTrendData(history(1), 'cpu', '30d', clusterMetrics)

    expect(chart.labels).toHaveLength(1)
    expect(chart.datasets).toHaveLength(TREND_DATASET_COUNT)
    chart.datasets.forEach((dataset) => {
      expect(dataset.data).toHaveLength(1)
      expect(dataset.pointRadius).toBeGreaterThan(0)
    })
  })

  it('still hides per-sample dots once the line itself is drawable', () => {
    const chart = createTrendData(history(8), 'cpu', '24h', clusterMetrics)

    expect(chart.datasets).toHaveLength(TREND_DATASET_COUNT)
    chart.datasets.forEach((dataset) => {
      expect(dataset.data).toHaveLength(8)
      expect(dataset.pointRadius).toBe(0)
    })
  })
})
