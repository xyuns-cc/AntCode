import { useRef, useState } from 'react'
import { theme } from 'antd'
import { globalMessage } from '@/hooks/useMessage'
import type { Chart } from 'chart.js'
import type { PerformancePeriod, WorkerDisplayData } from '../types'
import { useCurrentTime } from './useCurrentTime'
import { useMetricHistory } from './useMetricHistory'
import { useMonitorViewData } from './useMonitorViewData'
import { useTasks } from './useTasks'
import { useWorkers } from './useWorkers'

export const useMonitorController = () => {
  const { token } = theme.useToken()
  const currentTime = useCurrentTime()
  const [showAllWorkers, setShowAllWorkers] = useState(false)
  const [showAllAlerts, setShowAllAlerts] = useState(false)
  const [selectedWorker, setSelectedWorker] = useState<WorkerDisplayData | null>(null)
  const [performancePeriod, setPerformancePeriod] = useState<PerformancePeriod>('24h')
  const chartRef = useRef<Chart<'line'> | null>(null)
  const { loading, workers, lastChecked, refresh } = useWorkers()
  const { tasks, counts: taskCounts } = useTasks(workers)
  const { clusterHistory, workerHistory } = useMetricHistory(performancePeriod, selectedWorker)
  const view = useMonitorViewData({
    token,
    workers,
    taskCounts,
    lastChecked,
    period: performancePeriod,
    selectedWorker,
    clusterHistory,
    workerHistory,
  })

  // 失败提示由 refresh 内部弹出，这里只在真的成功时才报成功。
  const handleRefresh = async () => {
    if (await refresh(true)) globalMessage.success('数据刷新成功')
  }

  return {
    currentTime,
    loading,
    workers,
    lastChecked,
    tasks,
    view,
    chartRef,
    showAllWorkers,
    showAllAlerts,
    selectedWorker,
    performancePeriod,
    setShowAllWorkers,
    setShowAllAlerts,
    setSelectedWorker,
    setPerformancePeriod,
    handleRefresh,
  }
}
