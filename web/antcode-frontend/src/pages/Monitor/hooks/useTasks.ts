import { useEffect, useState } from 'react'
import { taskService } from '@/services/tasks'
import { dashboardService } from '@/services/dashboard'
import type { MonitorTask, MonitorTaskCounts, WorkerDisplayData } from '../types'

// 后端 TaskResponse（packages/antcode_core/.../domain/schemas/task.py）没有
// last_run_duration 字段，Pydantic 也不会输出未声明字段。原先读它只会恒定得到
// undefined，"运行时长"列因此永远是 '-'，看起来像"数据还没到"而不是"没有这个数据"。
interface TaskResponseItem {
  id: string
  name: string
  specified_worker_id?: string | null
  status: string
}

const mapTask = (task: TaskResponseItem, workers: WorkerDisplayData[]): MonitorTask => ({
  id: task.id,
  name: task.name,
  worker: workers.find((worker) => worker.id === task.specified_worker_id)?.name || '未分配',
  status:
    task.status === 'running'
      ? 'running'
      : task.status === 'failed'
        ? 'failed'
        : task.status === 'success'
          ? 'success'
          : 'pending',
  cpu: '-',
  memory: '-',
})

const EMPTY_COUNTS: MonitorTaskCounts = { success: 0, failed: 0, running: 0, pending: 0 }

export const useTasks = (workers: WorkerDisplayData[]) => {
  const [tasks, setTasks] = useState<MonitorTask[]>([])
  const [counts, setCounts] = useState<MonitorTaskCounts>(EMPTY_COUNTS)

  useEffect(() => {
    const loadTasks = async () => {
      try {
        const [response, summary] = await Promise.all([
          taskService.getTasks({ page: 1, size: 20 }),
          dashboardService.getDashboardStats(),
        ])
        setTasks((response.items || []).map((task) => mapTask(task, workers)))
        const { total, success, failed, running } = summary.tasks
        setCounts({
          success,
          failed,
          running,
          pending: Math.max(total - success - failed - running, 0),
        })
      } catch (error) {
        console.error('加载任务失败:', error)
      }
    }
    loadTasks()
  }, [workers])

  return { tasks, counts }
}
