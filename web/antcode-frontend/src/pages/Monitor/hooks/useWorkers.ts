import { useCallback, useEffect, useState } from 'react'
import { globalMessage } from '@/hooks/useMessage'
import { workerService } from '@/services/workers'
import { transformWorker } from '../data'
import type { WorkerDisplayData } from '../types'

const REFRESH_INTERVAL_MS = 10_000
const CHECKED_TIME_INTERVAL_MS = 60_000

export const useWorkers = () => {
  const [loading, setLoading] = useState(false)
  const [workers, setWorkers] = useState<WorkerDisplayData[]>([])
  const [lastChecked, setLastChecked] = useState('刚刚')

  const loadWorkers = useCallback(async (showLoading = true) => {
    if (showLoading) setLoading(true)
    try {
      const [allWorkers] = await Promise.all([
        workerService.getAllWorkers(),
        workerService.getAggregateStats(),
      ])
      setWorkers(allWorkers.map(transformWorker))
      setLastChecked('刚刚')
    } catch (error) {
      console.error('加载Worker 数据失败:', error)
      if (showLoading) globalMessage.error('加载Worker 数据失败')
    } finally {
      if (showLoading) setLoading(false)
    }
  }, [])

  useEffect(() => {
    loadWorkers()
  }, [loadWorkers])

  useEffect(() => {
    const interval = window.setInterval(() => {
      loadWorkers(false)
      setLastChecked('刚刚')
    }, REFRESH_INTERVAL_MS)
    return () => window.clearInterval(interval)
  }, [loadWorkers])

  useEffect(() => {
    const interval = window.setInterval(() => setLastChecked((previous) => {
      if (previous === '刚刚') return '1分钟前'
      const match = previous.match(/(\d+)分钟前/)
      if (!match) return previous
      const minutes = parseInt(match[1]) + 1
      return minutes >= 10 ? '10分钟前' : `${minutes}分钟前`
    }), CHECKED_TIME_INTERVAL_MS)
    return () => window.clearInterval(interval)
  }, [])

  return { loading, workers, lastChecked, refresh: loadWorkers }
}
