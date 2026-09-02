import { CloudServerOutlined } from '@ant-design/icons'
import { Drawer } from 'antd'
import type { Chart, ChartData, ChartOptions } from 'chart.js'
import type { RefObject } from 'react'
import type { MonitorTask, WorkerDisplayData } from '../types'
import { WorkerOverview } from './WorkerOverview'
import { WorkerTasksCard } from './WorkerTasksCard'
import { WorkerTrendCard } from './WorkerTrendCard'

interface WorkerDetailDrawerProps {
  worker: WorkerDisplayData | null
  // null = 任务列表这一路没取到（见 hooks/useTasks）；过滤不出行和过滤出零行是两回事。
  tasks: MonitorTask[] | null
  chartRef: RefObject<Chart<'line'> | null>
  chartData: ChartData<'line'> | null
  chartOptions: ChartOptions<'line'>
  onClose: () => void
}

// 比的是 MonitorTask.worker —— describeTaskWorkerBinding 给出的**绑定**文案，不是「这一次
// 实际跑在哪台」：后端任务列表没有实际执行 Worker，/tasks/running 的 worker_id 是 TaskRun
// 的内部自增 ID，和这里用的 public_id 对不上。所以 auto 策略的任务不会出现在任何一台的
// 抽屉里 —— 它们本来就没有绑定，这是正确的，卡片标题据此写成「绑定」。
export const WorkerDetailDrawer = (props: WorkerDetailDrawerProps) => {
  const workerTasks =
    props.worker && props.tasks
      ? props.tasks.filter((task) => task.worker === props.worker?.name)
      : null
  return (
    <Drawer
      title={<><CloudServerOutlined /> Worker 详情 - {props.worker?.name}</>}
      placement="right"
      width={600}
      onClose={props.onClose}
      open={!!props.worker}
    >
      {props.worker && (
        <div>
          <WorkerOverview worker={props.worker} />
          <WorkerTrendCard chartRef={props.chartRef} data={props.chartData} options={props.chartOptions} />
          <WorkerTasksCard tasks={workerTasks} />
        </div>
      )}
    </Drawer>
  )
}
