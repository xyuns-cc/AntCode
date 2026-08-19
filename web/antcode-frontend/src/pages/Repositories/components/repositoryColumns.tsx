import { Button, Popconfirm, Space, Tag, Typography } from 'antd'
import type { ColumnsType } from 'antd/es/table'
import {
  BranchesOutlined,
  DeleteOutlined,
  EditOutlined,
  FileSearchOutlined,
} from '@ant-design/icons'
import type { GitRepository } from '@/types/repository'
import { formatDate } from '@/utils/format'

const { Text } = Typography

interface RepositoryActions {
  onScan: (repository: GitRepository) => void
  onEdit: (repository: GitRepository) => void
  onDelete: (repository: GitRepository) => void
}

const URL_MAX_WIDTH = 420
const NAME_COLUMN_WIDTH = 180
const REF_COLUMN_WIDTH = 120
const STATUS_COLUMN_WIDTH = 120
const TIME_COLUMN_WIDTH = 180
const ACTION_COLUMN_WIDTH = 260

const scanStatusColor = (status: string | null): string => {
  if (status === 'failed') return 'red'
  if (status === 'success') return 'green'
  return 'default'
}

// 刻意写成普通函数而非组件：本文件导出的是列定义（非组件），
// 混入组件会触发 react-refresh/only-export-components。
const renderActions = (repository: GitRepository, actions: RepositoryActions) => (
  <Space size={0}>
    <Button type="link" size="small" icon={<FileSearchOutlined />} onClick={() => actions.onScan(repository)}>
      扫描导入
    </Button>
    <Button type="link" size="small" icon={<EditOutlined />} onClick={() => actions.onEdit(repository)}>
      编辑
    </Button>
    <Popconfirm
      title={`删除仓库 ${repository.name}`}
      description="已导入的项目不会被一起删除；仓库仍被项目引用时服务端会拒绝删除。"
      okText="删除"
      okButtonProps={{ danger: true }}
      cancelText="取消"
      onConfirm={() => actions.onDelete(repository)}
    >
      <Button type="link" size="small" danger icon={<DeleteOutlined />}>
        删除
      </Button>
    </Popconfirm>
  </Space>
)

export const buildRepositoryColumns = (actions: RepositoryActions): ColumnsType<GitRepository> => [
  {
    title: '仓库',
    dataIndex: 'name',
    width: NAME_COLUMN_WIDTH,
    render: (name: string, record) => (
      <Space direction="vertical" size={0}>
        <Text strong>{name}</Text>
        <Text code ellipsis style={{ maxWidth: URL_MAX_WIDTH }}>{record.url}</Text>
      </Space>
    ),
  },
  {
    title: '默认引用',
    dataIndex: 'default_ref',
    width: REF_COLUMN_WIDTH,
    render: (ref: string) => <Tag icon={<BranchesOutlined />}>{ref}</Tag>,
  },
  {
    title: '扫描状态',
    dataIndex: 'last_scan_status',
    width: STATUS_COLUMN_WIDTH,
    render: (status: string | null) => <Tag color={scanStatusColor(status)}>{status || '-'}</Tag>,
  },
  {
    title: '扫描时间',
    dataIndex: 'last_scanned_at',
    width: TIME_COLUMN_WIDTH,
    render: (value?: string | null) => value ? formatDate(value) : '-',
  },
  {
    title: '操作',
    key: 'actions',
    fixed: 'right',
    width: ACTION_COLUMN_WIDTH,
    render: (_, record) => renderActions(record, actions),
  },
]
