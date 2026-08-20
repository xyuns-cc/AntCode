import { Card, Tag, Tooltip } from 'antd'
import type { ColumnsType } from 'antd/es/table'
import ResponsiveTable from '@/components/common/ResponsiveTable'
import { getStatusColor, getStatusText } from '../status'
import type { MonitorTask } from '../types'

// 摘掉任务级 CPU/内存两列后只剩两列（外加 ResponsiveTable 注入的 70px 序号列）。宿主是
// 600px 宽的 Worker 详情抽屉，默认 800px 阈值本就撑出横向滚动条，按实际列宽收窄即可贴合。
const TABLE_MIN_WIDTH = 470

const columns: ColumnsType<MonitorTask> = [
  {
    title: '任务名称', dataIndex: 'name', key: 'name', width: 300, ellipsis: { showTitle: false },
    render: (name: string) => <Tooltip title={name} placement="topLeft"><span>{name}</span></Tooltip>,
  },
  {
    title: '状态', dataIndex: 'status', key: 'status', width: 100,
    render: (status: string) => <Tag color={getStatusColor(status)}>{getStatusText(status)}</Tag>,
  },
]

export const WorkerTasksCard = ({ tasks }: { tasks: MonitorTask[] }) => (
  <Card title="运行任务列表" style={{ marginTop: 16 }} size="small">
    <ResponsiveTable dataSource={tasks} rowKey="id" columns={columns} minWidth={TABLE_MIN_WIDTH} pagination={false} size="small" />
  </Card>
)
