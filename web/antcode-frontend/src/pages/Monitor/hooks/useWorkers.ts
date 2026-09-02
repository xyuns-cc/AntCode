import { useCallback, useEffect, useState } from 'react'
import { globalMessage } from '@/hooks/useMessage'
import { workerService } from '@/services/workers'
import { describeLastChecked, transformWorker } from '../data'
import type { WorkerDisplayData } from '../types'

const REFRESH_INTERVAL_MS = 10_000
const CHECKED_TIME_INTERVAL_MS = 60_000

export const useWorkers = () => {
  const [loading, setLoading] = useState(false)
  const [workers, setWorkers] = useState<WorkerDisplayData[]>([])
  const [lastSuccessAt, setLastSuccessAt] = useState<number | null>(null)
  // 每分钟走一格，只为让「N分钟前」自己变老；不携带任何业务含义。
  const [now, setNow] = useState(() => Date.now())

  // 返回值是「这次拉取成功了吗」：调用方据此决定报成功还是保持沉默，
  // 不能凭「调用发出去了」就弹成功。
  const loadWorkers = useCallback(async (showLoading = true): Promise<boolean> => {
    if (showLoading) setLoading(true)
    try {
      const allWorkers = await workerService.getAllWorkers()
      setWorkers(allWorkers.map(transformWorker))
      setLastSuccessAt(Date.now())
      setNow(Date.now())
      return true
    } catch (error) {
      console.error('加载Worker 数据失败:', error)
      if (showLoading) globalMessage.error('加载Worker 数据失败')
      return false
    } finally {
      if (showLoading) setLoading(false)
    }
  }, [])

  useEffect(() => {
    loadWorkers()
  }, [loadWorkers])

  useEffect(() => {
    const interval = window.setInterval(() => {
      void loadWorkers(false)
    }, REFRESH_INTERVAL_MS)
    return () => window.clearInterval(interval)
  }, [loadWorkers])

  useEffect(() => {
    const interval = window.setInterval(() => setNow(Date.now()), CHECKED_TIME_INTERVAL_MS)
    return () => window.clearInterval(interval)
  }, [])

  return {
    loading,
    workers,
    lastChecked: describeLastChecked(lastSuccessAt, now),
    refresh: loadWorkers,
  }
}
