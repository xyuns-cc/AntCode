import { useCallback, useEffect, useState } from 'react'
import { message } from 'antd'
import { workerService } from '@/services/workers'
import { getPerformancePeriodHours } from '../data'
import type {
  ClusterHistory,
  PerformancePeriod,
  WorkerDisplayData,
  WorkerHistoryPoint,
} from '../types'

const CLUSTER_REFRESH_INTERVAL_MS = 60_000
const WORKER_REFRESH_INTERVAL_MS = 30_000

export const useMetricHistory = (
  performancePeriod: PerformancePeriod,
  selectedWorker: WorkerDisplayData | null,
) => {
  const [clusterHistory, setClusterHistory] = useState<ClusterHistory | null>(null)
  const [workerHistory, setWorkerHistory] = useState<WorkerHistoryPoint[]>([])

  const loadClusterHistory = useCallback(async () => {
    try {
      setClusterHistory(await workerService.getClusterMetricsHistory(
        getPerformancePeriodHours(performancePeriod),
      ))
    } catch (error) {
      console.error('加载集群历史指标失败:', error)
      message.error(error instanceof Error ? error.message : '加载集群历史指标失败')
    }
  }, [performancePeriod])

  const loadWorkerHistory = useCallback(async (workerId: string) => {
    try {
      setWorkerHistory(await workerService.getWorkerMetricsHistory(workerId, 720))
    } catch (error) {
      console.error('加载Worker历史指标失败:', error)
      message.error(error instanceof Error ? error.message : '加载Worker历史指标失败')
    }
  }, [])

  useEffect(() => {
    loadClusterHistory()
    const interval = window.setInterval(loadClusterHistory, CLUSTER_REFRESH_INTERVAL_MS)
    return () => window.clearInterval(interval)
  }, [loadClusterHistory])

  useEffect(() => {
    if (!selectedWorker) return
    loadWorkerHistory(selectedWorker.id)
    const interval = window.setInterval(
      () => loadWorkerHistory(selectedWorker.id),
      WORKER_REFRESH_INTERVAL_MS,
    )
    return () => window.clearInterval(interval)
  }, [selectedWorker, loadWorkerHistory])

  return { clusterHistory, workerHistory }
}
