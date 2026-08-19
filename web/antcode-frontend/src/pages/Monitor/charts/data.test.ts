import { describe, expect, it } from 'vitest'

import { createTaskStatsData, createTrendData } from './data'
import type { ClusterHistory } from '../types'

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
describe('createTrendData keeps a single history point visible', () => {
  it('gives every dataset a drawable point radius when there is one sample', () => {
    const chart = createTrendData(history(1), 'cpu', '30d', clusterMetrics)

    expect(chart.labels).toHaveLength(1)
    chart.datasets.forEach((dataset) => {
      expect(dataset.data).toHaveLength(1)
      expect(dataset.pointRadius).toBeGreaterThan(0)
    })
  })

  it('still hides per-sample dots once the line itself is drawable', () => {
    const chart = createTrendData(history(8), 'cpu', '24h', clusterMetrics)

    chart.datasets.forEach((dataset) => {
      expect(dataset.pointRadius).toBe(0)
    })
  })
})
