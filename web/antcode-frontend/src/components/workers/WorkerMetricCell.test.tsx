/**
 * 指标格必须把"读不回来"和"还没心跳"画成两个东西。
 *
 * 后端对读不回来的列返回 metrics=null + snapshotErrors；对没心跳过的机器返回
 * metrics=null 且 snapshotErrors 为空。两者都渲染成 '—' 的话，键集错配就被伪装成
 * "刚接进来的新机器"，没人会去补 schema —— 这正是 25d4c34 之前"页面全是 0"那类
 * 故障的换皮版本。
 */
import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import type { Worker, WorkerMetrics } from '@/types'
import { renderMetricCell, NO_METRICS_PLACEHOLDER } from './WorkerMetricCell'

const CPU_PERCENT = 26.3
const UNKNOWN_KEY = 'gpuUtilization'

const BASE_WORKER: Worker = {
  id: 'mn-worker-01',
  name: 'mn-worker-01',
  host: '127.0.0.1',
  port: 8001,
  status: 'online',
  createdAt: '2026-07-14T00:00:00Z'
}

const HEALTHY_METRICS: WorkerMetrics = {
  cpu: CPU_PERCENT,
  memory: 0,
  disk: 0,
  taskCount: 0,
  runningTasks: 0,
  projectCount: 0,
  envCount: 0,
  uptime: 0
}

function renderCell(worker: Worker) {
  return render(<div>{renderMetricCell(worker, (metrics) => <span>{metrics.cpu.toFixed(1)}%</span>)}</div>)
}

describe('renderMetricCell', () => {
  it('读回失败时渲染"读取失败"并把键名带进 title', () => {
    renderCell({
      ...BASE_WORKER,
      metrics: null,
      snapshotErrors: [
        { column: 'metrics', keys: [UNKNOWN_KEY], message: `${UNKNOWN_KEY}: Extra inputs are not permitted` }
      ]
    })

    expect(screen.getByText('读取失败')).toBeInTheDocument()
    expect(screen.queryByText(NO_METRICS_PLACEHOLDER)).not.toBeInTheDocument()
  })

  it('没心跳过的机器仍是占位符，不冒充故障', () => {
    renderCell({ ...BASE_WORKER, metrics: null })

    expect(screen.getByText(NO_METRICS_PLACEHOLDER)).toBeInTheDocument()
    expect(screen.queryByText('读取失败')).not.toBeInTheDocument()
  })

  it('控制组：有指标时照常渲染真值', () => {
    renderCell({ ...BASE_WORKER, metrics: HEALTHY_METRICS })

    expect(screen.getByText(`${CPU_PERCENT.toFixed(1)}%`)).toBeInTheDocument()
    expect(screen.queryByText('读取失败')).not.toBeInTheDocument()
  })
})
