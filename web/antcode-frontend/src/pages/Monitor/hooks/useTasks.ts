import { useEffect, useState } from 'react'
import { taskService } from '@/services/tasks'
import { dashboardService } from '@/services/dashboard'
import { describeTaskWorkerBinding, type TaskWorkerBindingFields } from '@/utils/taskWorkerBinding'
import type { MonitorTask, MonitorTaskCounts } from '../types'

// 这里只映射后端 TaskResponse（packages/antcode_core/.../domain/schemas/task.py）真有的
// 字段。该 schema 不提供任何任务级运行数据，历次想读的都恒定落空：
//
// - last_run_duration：schema 里没有此字段，Pydantic 也不会输出未声明字段，读它恒得
//   undefined，"运行时长"列因此永远是 '-'（0706582 已摘）。
// - cpu / memory：连读都没读过，自 mock 版改真接口起就直接写死 '-'。任务级用量只在 Worker
//   进程内采样（executor/resource_sampler.py）当超限判据，engine.py::_task_result 组装上报
//   时并不带出来；task_executions 的同名列也因恒 NULL 被摘（e7b7249）。要"补"等于新造一条
//   Worker→Master→API 的上报链路，那是做功能，不是填值。
//
// 共同的教训：留着永远取不到值的列，只会把"产品没有这个数据"伪装成"数据还没加载到"。
interface TaskResponseItem extends TaskWorkerBindingFields {
  id: string
  name: string
  status: string
}

const mapTask = (task: TaskResponseItem): MonitorTask => ({
  id: task.id,
  name: task.name,
  worker: describeTaskWorkerBinding(task),
  status:
    task.status === 'running'
      ? 'running'
      : task.status === 'failed'
        ? 'failed'
        : task.status === 'success'
          ? 'success'
          : 'pending',
})

const EMPTY_COUNTS: MonitorTaskCounts = { success: 0, failed: 0, running: 0, pending: 0 }

// 参数只当刷新节拍：Worker 列表每 10 秒换一次引用，任务表跟着重取。映射本身不再需要
// 它——绑定文案取自 TaskResponse 自带的 *_worker_name，不用再拿 id 去 Worker 列表里反查。
export const useTasks = (refreshSignal: unknown) => {
  const [tasks, setTasks] = useState<MonitorTask[]>([])
  const [counts, setCounts] = useState<MonitorTaskCounts>(EMPTY_COUNTS)

  useEffect(() => {
    const loadTasks = async () => {
      try {
        const [response, summary] = await Promise.all([
          taskService.getTasks({ page: 1, size: 20 }),
          dashboardService.getDashboardStats(),
        ])
        setTasks((response.items || []).map(mapTask))
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
  }, [refreshSignal])

  return { tasks, counts }
}
