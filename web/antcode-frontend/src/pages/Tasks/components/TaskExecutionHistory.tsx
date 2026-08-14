import { useMemo } from 'react'
import { Button, Space, Tag, Tooltip } from 'antd'
import type { ColumnsType } from 'antd/es/table'
import { EyeOutlined, RedoOutlined, ReloadOutlined, StopOutlined } from '@ant-design/icons'
import { useNavigate } from 'react-router'
import ResponsiveTable from '@/components/common/ResponsiveTable'
import type { TaskExecution, TaskStatus } from '@/types'
import { formatDateTime, formatDuration, formatStatus } from '@/utils/format'

const EXECUTION_COLUMNS: ColumnsType<TaskExecution> = [
  {
    title: '执行ID',
    dataIndex: 'run_id',
    key: 'run_id',
    width: 120,
    ellipsis: { showTitle: false },
    render: (text: string) => (
      <Tooltip title={text}>
        <code style={{ fontSize: 12 }}>{text.substring(0, 8)}...</code>
      </Tooltip>
    ),
  },
  {
    title: '状态',
    dataIndex: 'status',
    key: 'status',
    width: 90,
    render: (value: TaskStatus) => {
      const status = formatStatus(value)
      return <Tag color={status.color}>{status.text}</Tag>
    },
  },
  {
    title: '开始时间',
    dataIndex: 'start_time',
    key: 'start_time',
    width: 160,
    render: (value: string) => formatDateTime(value),
  },
  {
    title: '结束时间',
    dataIndex: 'end_time',
    key: 'end_time',
    width: 160,
    render: (value: string) => (value ? formatDateTime(value) : '-'),
  },
  {
    title: '持续时间',
    dataIndex: 'duration_seconds',
    key: 'duration_seconds',
    width: 100,
    render: (value: number) => (value ? formatDuration(value) : '-'),
  },
  {
    title: '退出码',
    dataIndex: 'exit_code',
    key: 'exit_code',
    width: 80,
    render: (value: number | null) => value ?? '-',
  },
  { title: '重试', dataIndex: 'retry_count', key: 'retry_count', width: 60 },
]

interface TaskExecutionHistoryProps {
  taskId: string
  executions: TaskExecution[]
  loading: boolean
  page: number
  size: number
  total: number
  retryLoading: string | null
  cancelLoading: string | null
  onReload: () => void
  onPageChange: (page: number, size: number) => void
  onRetry: (runId: string) => void
  onCancel: (runId: string) => void
}

export const TaskExecutionHistory = (props: TaskExecutionHistoryProps) => {
  const navigate = useNavigate()
  const columns = useMemo<ColumnsType<TaskExecution>>(
    () => [
      ...EXECUTION_COLUMNS,
      {
        title: '操作',
        key: 'actions',
        width: 180,
        fixed: 'right',
        render: (_value, record) => (
          <ExecutionActions
            record={record}
            retryLoading={props.retryLoading}
            cancelLoading={props.cancelLoading}
            onLogs={() => navigate(`/tasks/${props.taskId}/runs/${record.run_id}`)}
            onRetry={props.onRetry}
            onCancel={props.onCancel}
          />
        ),
      },
    ],
    [navigate, props]
  )

  return (
    <div>
      <div style={{ marginBottom: 16 }}>
        <Button icon={<ReloadOutlined />} onClick={props.onReload} loading={props.loading}>
          刷新
        </Button>
      </div>
      <ResponsiveTable
        rowKey="id"
        dataSource={props.executions}
        columns={columns}
        loading={props.loading}
        pagination={{
          current: props.page,
          pageSize: props.size,
          total: props.total,
          showSizeChanger: true,
          showQuickJumper: true,
          onChange: props.onPageChange,
        }}
      />
    </div>
  )
}

interface ExecutionActionsProps {
  record: TaskExecution
  retryLoading: string | null
  cancelLoading: string | null
  onLogs: () => void
  onRetry: (runId: string) => void
  onCancel: (runId: string) => void
}

const ExecutionActions = (props: ExecutionActionsProps) => {
  const { record } = props
  const cancellable = ['running', 'pending', 'queued', 'dispatching'].includes(record.status)
  const retryable = ['failed', 'timeout', 'rejected'].includes(record.status)
  return (
    <Space size="small">
      <Button type="text" size="small" icon={<EyeOutlined />} onClick={props.onLogs}>
        日志
      </Button>
      {cancellable && (
        <Tooltip title="取消执行">
          <Button
            type="text"
            size="small"
            danger
            icon={<StopOutlined />}
            loading={props.cancelLoading === record.run_id}
            onClick={() => props.onCancel(record.run_id)}
          >
            取消
          </Button>
        </Tooltip>
      )}
      {retryable && (
        <Tooltip title="重试此执行">
          <Button
            type="text"
            size="small"
            icon={<RedoOutlined />}
            loading={props.retryLoading === record.run_id}
            onClick={() => props.onRetry(record.run_id)}
          >
            重试
          </Button>
        </Tooltip>
      )}
    </Space>
  )
}
