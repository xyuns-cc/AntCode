import { useMemo } from 'react'
import type { GlobalToken } from 'antd/es/theme/interface'
import {
  calculateClusterMetrics,
  createDiskUsageData,
  createTaskStatsData,
  createTrendData,
  createWorkerDetailData,
} from '../charts/data'
import {
  createChartOptions,
  createDiskBarOptions,
  createTaskBarOptions,
  createWorkerDetailOptions,
} from '../charts/options'
import { calculateMonitorStats, createAlerts } from '../data'
import type {
  ClusterHistory,
  MonitorTask,
  MonitorTaskCounts,
  PerformancePeriod,
  WorkerDisplayData,
  WorkerHistoryPoint,
} from '../types'

interface ViewDataInput {
  token: GlobalToken
  workers: WorkerDisplayData[]
  tasks: MonitorTask[]
  taskCounts: MonitorTaskCounts
  lastChecked: string
  period: PerformancePeriod
  selectedWorker: WorkerDisplayData | null
  clusterHistory: ClusterHistory | null
  workerHistory: WorkerHistoryPoint[]
}

export const useMonitorViewData = (input: ViewDataInput) => {
  const alerts = useMemo(
    () => createAlerts(input.workers, input.lastChecked),
    [input.workers, input.lastChecked]
  )
  const stats = useMemo(() => calculateMonitorStats(input.workers), [input.workers])
  const clusterMetrics = useMemo(() => calculateClusterMetrics(input.workers), [input.workers])
  const cpuData = useMemo(
    () => createTrendData(input.clusterHistory, 'cpu', input.period, clusterMetrics),
    [input.clusterHistory, input.period, clusterMetrics]
  )
  const memoryData = useMemo(
    () => createTrendData(input.clusterHistory, 'memory', input.period, clusterMetrics),
    [input.clusterHistory, input.period, clusterMetrics]
  )
  const workerData = useMemo(
    () => createWorkerDetailData(input.selectedWorker, input.workerHistory),
    [input.selectedWorker, input.workerHistory]
  )
  const taskStatsData = useMemo(() => createTaskStatsData(input.taskCounts), [input.taskCounts])
  const diskUsageData = useMemo(() => createDiskUsageData(input.workers), [input.workers])
  const chartOptions = useMemo(() => createChartOptions(input.token), [input.token])
  const taskBarOptions = useMemo(() => createTaskBarOptions(chartOptions), [chartOptions])
  const diskBarOptions = useMemo(() => createDiskBarOptions(chartOptions), [chartOptions])
  const workerOptions = useMemo(() => createWorkerDetailOptions(input.token), [input.token])
  return {
    alerts,
    stats,
    cpuData,
    memoryData,
    workerData,
    taskStatsData,
    diskUsageData,
    chartOptions,
    taskBarOptions,
    diskBarOptions,
    workerOptions,
  }
}
