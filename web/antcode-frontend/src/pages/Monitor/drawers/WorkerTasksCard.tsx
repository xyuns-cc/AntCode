import { Card, Tag, Tooltip } from 'antd'
import type { ColumnsType } from 'antd/es/table'
import ResponsiveTable from '@/components/common/ResponsiveTable'
import { getStatusColor, getStatusText } from '../status'
import type { MonitorTask } from '../types'

const columns: ColumnsType<MonitorTask> = [
  {
    title: '任务名称', dataIndex: 'name', key: 'name', width: 150, ellipsis: { showTitle: false },
    render: (name: string) => <Tooltip title={name} placement="topLeft"><span>{name}</span></Tooltip>,
  },
  {
    title: '状态', dataIndex: 'status', key: 'status', width: 80,
    render: (status: string) => <Tag color={getStatusColor(status)}>{getStatusText(status)}</Tag>,
  },
  {
    title: 'CPU', dataIndex: 'cpu', key: 'cpu', width: 60,
    render: (cpu: number | string) => typeof cpu === 'number' ? `${cpu}%` : cpu,
  },
  {
    title: '内存', dataIndex: 'memory', key: 'memory', width: 60,
    render: (memory: number | string) => typeof memory === 'number' ? `${memory}%` : memory,
  },
]

export const WorkerTasksCard = ({ tasks }: { tasks: MonitorTask[] }) => (
  <Card title="运行任务列表" style={{ marginTop: 16 }} size="small">
    <ResponsiveTable dataSource={tasks} rowKey="id" columns={columns} pagination={false} size="small" />
  </Card>
)
