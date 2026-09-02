import { Card, Tag, Tooltip, Typography } from 'antd'
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

// 标题说「绑定」而不是「运行」：过滤依据是任务的 Worker 绑定配置（见 WorkerDetailDrawer），
// 且状态不限，行里跑没跑看「状态」列。副标题写明数据源只有任务列表前 20 条 —— 这份清单是
// 那个截断窗口的子集，不是该 Worker 的全部绑定任务，不说明就是把残缺当完整。
export const WorkerTasksCard = ({ tasks }: { tasks: MonitorTask[] | null }) => (
  <Card
    title="绑定到该 Worker 的任务"
    extra={<Typography.Text type="secondary" style={{ fontSize: 12 }}>仅取自任务列表前 20 条</Typography.Text>}
    style={{ marginTop: 16 }}
    size="small"
  >
    <ResponsiveTable
      dataSource={tasks ?? undefined}
      emptyDescription={tasks === null ? '任务列表加载失败' : '暂无数据'}
      rowKey="id" columns={columns} minWidth={TABLE_MIN_WIDTH} pagination={false} size="small"
    />
  </Card>
)
