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

// 参数只当刷新节拍：Worker 列表每 10 秒换一次引用，任务表跟着重取。映射本身不再需要
// 它——绑定文案取自 TaskResponse 自带的 *_worker_name，不用再拿 id 去 Worker 列表里反查。
//
// 两个返回值都用 `null` 表示「这一路没取到」，与「后端说真的没有任务」（空数组 / 全 0）
// 分开。过去两路走 Promise.all 且整块 catch：任一路挂掉，表格就保持空或保持上一轮的陈旧
// 数据，除了一行 console.error 之外界面上没有任何可见信号。
export const useTasks = (refreshSignal: unknown) => {
  const [tasks, setTasks] = useState<MonitorTask[] | null>(null)
  const [counts, setCounts] = useState<MonitorTaskCounts | null>(null)

  useEffect(() => {
    const loadTasks = async () => {
      // 各自成败：任务列表挂了不该顺带把还拿得到的状态汇总也抹掉，反之亦然。
      const [listed, summarized] = await Promise.allSettled([
        taskService.getTasks({ page: 1, size: 20 }),
        dashboardService.getDashboardStats(),
      ])
      if (listed.status === 'fulfilled') {
        setTasks((listed.value.items || []).map(mapTask))
      } else {
        console.error('加载任务列表失败:', listed.reason)
        setTasks(null)
      }
      if (summarized.status === 'fulfilled') {
        const { total, success, failed, running } = summarized.value.tasks
        setCounts({
          success,
          failed,
          running,
          pending: Math.max(total - success - failed - running, 0),
        })
      } else {
        console.error('加载任务统计失败:', summarized.reason)
        setCounts(null)
      }
    }
    loadTasks()
  }, [refreshSignal])

  return { tasks, counts }
}
