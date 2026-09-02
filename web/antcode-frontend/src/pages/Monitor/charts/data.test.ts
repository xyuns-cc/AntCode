import { describe, expect, it } from 'vitest'

import { calculateClusterMetrics, createTaskStatsData, createTrendData } from './data'
import type { ClusterHistory, WorkerDisplayData } from '../types'

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
