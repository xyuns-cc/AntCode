import { useEffect, useState } from 'react'
import { taskService } from '@/services/tasks'
import type { MonitorTask, WorkerDisplayData } from '../types'

interface TaskResponseItem {
  id: string
  name: string
  specified_worker_id?: string
  status: string
  last_run_duration?: number
}

const mapTask = (task: TaskResponseItem, workers: WorkerDisplayData[]): MonitorTask => ({
  id: task.id,
  name: task.name,
  worker: workers.find((worker) => worker.id === task.specified_worker_id)?.name || '未分配',
  status: task.status === 'running'
    ? 'running'
    : task.status === 'failed'
      ? 'failed'
      : task.status === 'success' ? 'success' : 'pending',
  cpu: '-',
  memory: '-',
  duration: task.last_run_duration ? `${Math.round(task.last_run_duration)}秒` : '-',
})

export const useTasks = (workers: WorkerDisplayData[]): MonitorTask[] => {
  const [tasks, setTasks] = useState<MonitorTask[]>([])

  useEffect(() => {
    const loadTasks = async () => {
      try {
        const response = await taskService.getTasks({ page: 1, size: 20 })
        setTasks((response.items || []).map((task) => mapTask(task, workers)))
      } catch (error) {
        console.error('加载任务失败:', error)
      }
    }
    loadTasks()
  }, [workers])

  return tasks
}
