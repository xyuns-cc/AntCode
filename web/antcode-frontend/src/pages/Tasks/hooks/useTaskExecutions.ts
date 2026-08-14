import { useCallback, useEffect, useState } from 'react'
import { taskService } from '@/services/tasks'
import type { TaskExecution } from '@/types'
import Logger from '@/utils/logger'

const DEFAULT_PAGE_SIZE = 20

interface ExecutionPagination {
  page: number
  size: number
  total: number
}

export const useTaskExecutions = (taskId?: string) => {
  const [executions, setExecutions] = useState<TaskExecution[]>([])
  const [loading, setLoading] = useState(false)
  const [pagination, setPagination] = useState<ExecutionPagination>({
    page: 1,
    size: DEFAULT_PAGE_SIZE,
    total: 0,
  })

  const load = useCallback(async () => {
    if (!taskId) return
    setLoading(true)
    try {
      const result = await taskService.getTaskRuns(taskId, {
        page: pagination.page,
        size: pagination.size,
      })
      setExecutions(result.items)
      setPagination((current) => ({ ...current, total: result.total }))
    } catch (error) {
      Logger.error('加载任务执行记录失败', error)
    } finally {
      setLoading(false)
    }
  }, [pagination.page, pagination.size, taskId])

  useEffect(() => {
    void load()
  }, [load])

  const changePage = useCallback((page: number, size: number) => {
    setPagination((current) => ({
      page: size === current.size ? page : 1,
      size,
      total: current.total,
    }))
  }, [])

  return { executions, loading, pagination, load, changePage }
}
