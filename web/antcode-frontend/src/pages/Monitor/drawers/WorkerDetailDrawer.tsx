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
  tasks: MonitorTask[]
  chartRef: RefObject<Chart<'line'> | null>
  chartData: ChartData<'line'> | null
  chartOptions: ChartOptions<'line'>
  onClose: () => void
}

export const WorkerDetailDrawer = (props: WorkerDetailDrawerProps) => {
  const workerTasks = props.worker ? props.tasks.filter((task) => task.worker === props.worker?.name) : []
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
