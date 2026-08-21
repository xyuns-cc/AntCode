import { BugOutlined, DatabaseOutlined, HddOutlined } from '@ant-design/icons'
import { Button, Card, Col, Row, Tag, Tooltip } from 'antd'
import type { ColumnsType } from 'antd/es/table'
import type { ChartData, ChartOptions } from 'chart.js'
import { Bar } from 'react-chartjs-2'
import ResponsiveTable from '@/components/common/ResponsiveTable'
import { getStatusColor, getStatusText } from '../status'
import type { MonitorTask } from '../types'

interface TasksSectionProps {
  tasks: MonitorTask[]
  taskStatsData: ChartData<'bar'>
  diskUsageData: ChartData<'bar'>
  taskBarOptions: ChartOptions<'bar'>
  diskBarOptions: ChartOptions<'bar'>
  onViewTask: (taskId: string) => void
}

// 摘掉任务级 CPU/内存两列后，剩下四列（含 ResponsiveTable 注入的 70px 序号列）实际只占
// 600px，沿用默认的 800px 阈值会让列被无谓拉伸、窄屏还平白横向滚动。
const TABLE_MIN_WIDTH = 600

const buildColumns = (onViewTask: (taskId: string) => void): ColumnsType<MonitorTask> => [
  {
    title: '任务名称', dataIndex: 'name', key: 'name', width: 220, ellipsis: { showTitle: false },
    render: (name: string) => <Tooltip title={name} placement="topLeft"><span>{name}</span></Tooltip>,
  },
  { title: '执行 Worker', dataIndex: 'worker', key: 'worker', width: 140 },
  {
    title: '状态', dataIndex: 'status', key: 'status', width: 100,
    render: (status: string) => <Tag color={getStatusColor(status)}>{getStatusText(status)}</Tag>,
  },
  {
    title: '操作', key: 'action', width: 70, fixed: 'right',
    render: (_value: unknown, task: MonitorTask) => (
      <Button type="link" size="small" onClick={() => onViewTask(task.id)}>详情</Button>
    ),
  },
]

// 卡片右上角原先挂着一个「筛选」按钮（图标还是 SyncOutlined），从初版起就没有 onClick。
// 它也不该被补上：这张表的数据源 useTasks 固定取 `page:1,size:20`，任何本地筛选都只作用在
// 这 20 条截断窗口上——按"失败"筛出来的不是全部失败任务，而是"前 20 条里失败的"，属于把
// 残缺结果伪装成完整结果。真正的任务筛选在 /tasks 页（状态/项目/Worker 三个 Select + 搜索，
// 走后端分页），这里不再重造一个会骗人的半成品。
const TaskTable = ({ tasks, onViewTask }: { tasks: MonitorTask[]; onViewTask: (taskId: string) => void }) => (
  <Card size="small" title={<span style={{ fontSize: 14 }}><BugOutlined /> 关键任务状态</span>}>
    <ResponsiveTable dataSource={tasks} rowKey="id" columns={buildColumns(onViewTask)} minWidth={TABLE_MIN_WIDTH} pagination={{ pageSize: 5, size: 'small' }} size="small" />
  </Card>
)

const StatsCharts = ({ taskData, diskData, taskOptions, diskOptions }: {
  taskData: ChartData<'bar'>
  diskData: ChartData<'bar'>
  taskOptions: ChartOptions<'bar'>
  diskOptions: ChartOptions<'bar'>
}) => (
  <>
    <Card size="small" title={<span style={{ fontSize: 14 }}><DatabaseOutlined /> 任务执行统计</span>} style={{ marginBottom: 12 }}>
      <div style={{ height: 180 }}><Bar data={taskData} options={taskOptions} /></div>
    </Card>
    <Card size="small" title={<span style={{ fontSize: 14 }}><HddOutlined /> 各Worker磁盘使用率</span>}>
      <div style={{ height: 180 }}><Bar data={diskData} options={diskOptions} /></div>
    </Card>
  </>
)

export const TasksSection = (props: TasksSectionProps) => (
  <Row gutter={12}>
    <Col xs={24} lg={16}><TaskTable tasks={props.tasks} onViewTask={props.onViewTask} /></Col>
    <Col xs={24} lg={8}>
      <StatsCharts
        taskData={props.taskStatsData}
        diskData={props.diskUsageData}
        taskOptions={props.taskBarOptions}
        diskOptions={props.diskBarOptions}
      />
    </Col>
  </Row>
)
