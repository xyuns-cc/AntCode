import { useCallback, useEffect, useRef, useState } from 'react'
import { runsService, type SpiderItem } from '@/services/runs'
import type { TaskStatus } from '@/types'
import Logger from '@/utils/logger'
import { isTerminalTaskStatus } from '@/utils/taskStatus'

const SPIDER_ITEM_PAGE_SIZE = 100

export const useSpiderItems = (runId: string, status?: TaskStatus) => {
  const requestId = useRef(0)
  const [items, setItems] = useState<SpiderItem[]>([])
  const [lastId, setLastId] = useState('0')
  const [hasMore, setHasMore] = useState(false)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const fetchPage = useCallback(
    async (startId: string, replace: boolean) => {
      const currentRequest = ++requestId.current
      setLoading(true)
      try {
        const response = await runsService.listSpiderItems(runId, {
          startId,
          count: SPIDER_ITEM_PAGE_SIZE,
        })
        if (currentRequest !== requestId.current) return
        setItems((current) => (replace ? response.items : appendUnique(current, response.items)))
        setLastId(response.last_id)
        setHasMore(response.has_more)
        setError(null)
      } catch (fetchError) {
        if (currentRequest !== requestId.current) return
        Logger.warn('获取抓取数据失败', fetchError)
        if (replace) setItems([])
        setError(fetchError instanceof Error ? fetchError.message : '获取抓取数据失败')
      } finally {
        if (currentRequest === requestId.current) setLoading(false)
      }
    },
    [runId]
  )

  const refresh = useCallback(() => fetchPage('0', true), [fetchPage])
  const loadMore = useCallback(() => fetchPage(lastId, false), [fetchPage, lastId])
  const terminal = status ? isTerminalTaskStatus(status) : false

  useEffect(() => {
    void refresh()
    return () => {
      requestId.current += 1
    }
  }, [refresh, terminal])

  return { items, hasMore, loading, error, refresh, loadMore }
}

const appendUnique = (current: SpiderItem[], added: SpiderItem[]): SpiderItem[] => {
  const ids = new Set(current.map((item) => item._id))
  return [...current, ...added.filter((item) => !ids.has(item._id))]
}
