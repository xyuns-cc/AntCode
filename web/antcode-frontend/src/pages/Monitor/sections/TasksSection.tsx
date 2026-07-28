import { BugOutlined, DatabaseOutlined, HddOutlined, SyncOutlined } from '@ant-design/icons'
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
}

const columns: ColumnsType<MonitorTask> = [
  {
    title: '任务名称', dataIndex: 'name', key: 'name', width: 150, ellipsis: { showTitle: false },
    render: (name: string) => <Tooltip title={name} placement="topLeft"><span>{name}</span></Tooltip>,
  },
  { title: '执行 Worker', dataIndex: 'worker', key: 'worker', width: 100 },
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
  { title: '运行时长', dataIndex: 'duration', key: 'duration', width: 90 },
  { title: '操作', key: 'action', width: 70, fixed: 'right', render: () => <Button type="link" size="small">详情</Button> },
]

const TaskTable = ({ tasks }: { tasks: MonitorTask[] }) => (
  <Card
    size="small"
    title={<span style={{ fontSize: 14 }}><BugOutlined /> 关键任务状态</span>}
    extra={<Button size="small" icon={<SyncOutlined />} style={{ fontSize: 12 }}>筛选</Button>}
  >
    <ResponsiveTable dataSource={tasks} rowKey="id" columns={columns} pagination={{ pageSize: 5, size: 'small' }} size="small" />
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
    <Col xs={24} lg={16}><TaskTable tasks={props.tasks} /></Col>
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
