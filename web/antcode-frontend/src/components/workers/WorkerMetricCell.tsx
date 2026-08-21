/**
 * Worker 列表里一格指标的渲染。
 *
 * 三种状态必须互相区分，塌成同一个 '—' 就等于把 bug 藏起来：
 * - 有指标：正常渲染；
 * - metrics 为空且没有 snapshotErrors：这台机器还没心跳，'—' 是对的；
 * - metrics 为空且有 snapshotErrors：控制面 schema 与 Worker 二进制键集错配，
 *   得有人去补 schema。渲染成红色"读取失败"并把键名放进 tooltip。
 *
 * 详情抽屉同理单独渲染 snapshotErrors：否则读不回来的列在那里只表现为"资源面板
 * 不见了"，与一台还没心跳的机器同形。
 */
import React from 'react'
import { Tag, Tooltip } from 'antd'
import type { Worker, WorkerMetrics } from '@/types'
import {
  METRICS_COLUMN,
  SNAPSHOT_ERROR_LABEL,
  snapshotErrorFor,
  snapshotErrorTooltip,
} from './workerSnapshotError'

export const NO_METRICS_PLACEHOLDER = '—'

export function renderMetricCell(worker: Worker, render: (metrics: WorkerMetrics) => React.ReactNode): React.ReactNode {
  const error = snapshotErrorFor(worker.snapshotErrors, METRICS_COLUMN)
  if (error) {
    return (
      <Tooltip title={snapshotErrorTooltip(error)} placement="topLeft">
        <Tag color="error">{SNAPSHOT_ERROR_LABEL}</Tag>
      </Tooltip>
    )
  }
  return worker.metrics ? render(worker.metrics) : NO_METRICS_PLACEHOLDER
}
