import { Button, Space, Tag, Tooltip } from 'antd'
import { CloudServerOutlined, DeleteOutlined, DownloadOutlined, EditOutlined, EyeOutlined } from '@ant-design/icons'
import CopyableTooltip from '@/components/common/CopyableTooltip'
import { getScopeDisplay } from '@/config/displayConfig'
import type { ExtendedRuntimeEnvItem } from './types'

interface EnvColumnActions {
  onViewPackages: (record: ExtendedRuntimeEnvItem) => void
  onEdit: (record: ExtendedRuntimeEnvItem) => void
  onInstall: (record: ExtendedRuntimeEnvItem) => void
  onDelete: (record: ExtendedRuntimeEnvItem) => void
}

const renderWorkerTag = (name?: string) => (
  <Tooltip title={name || '未知'} placement="topLeft">
    <Tag color="cyan" style={{ maxWidth: '100%', overflow: 'hidden', textOverflow: 'ellipsis' }}>
      <CloudServerOutlined style={{ fontSize: 12, marginRight: 4 }} />
      {name || '未知'}
    </Tag>
  </Tooltip>
)

export const buildEnvColumns = (actions: EnvColumnActions) => [
  {
    title: 'Worker',
    dataIndex: 'workerName',
    key: 'workerName',
    width: 140,
    ellipsis: true,
    render: (name?: string) => renderWorkerTag(name),
  },
  {
    title: '作用域',
    dataIndex: 'scope',
    key: 'scope',
    width: 80,
    render: (value: string) => {
      const display = getScopeDisplay(value)
      return <Tag color={display.color}>{display.label}</Tag>
    },
  },
  {
    title: '名称',
    dataIndex: 'key',
    key: 'key',
    width: 180,
    ellipsis: true,
    render: (value?: string) => value ? <Tooltip title={value}><span>{value}</span></Tooltip> : '-',
  },
  {
    title: 'Python',
    dataIndex: 'version',
    key: 'version',
    width: 100,
    render: (value: string) => <Tag color="blue">{value}</Tag>,
  },
  {
    title: '路径',
    dataIndex: 'runtime_locator',
    key: 'runtime_locator',
    ellipsis: true,
    render: (value: string) => (
      <CopyableTooltip text={value}>
        <span style={{ cursor: 'pointer' }}>{value}</span>
      </CopyableTooltip>
    ),
  },
  {
    title: '创建人',
    dataIndex: 'created_by_username',
    key: 'created_by_username',
    width: 90,
    render: (value?: string | null) => value || '-',
  },
  {
    title: '创建时间',
    dataIndex: 'created_at',
    key: 'created_at',
    width: 100,
    render: (value?: string | null) => {
      if (!value) return '-'
      const date = String(value).split('T')[0]
      const time = String(value).split('T')[1]?.split('.')[0] || ''
      return <Tooltip title={`${date} ${time}`}><span>{date}</span></Tooltip>
    },
  },
  {
    title: '操作',
    key: 'actions',
    width: 280,
    fixed: 'right' as const,
    render: (_: unknown, record: ExtendedRuntimeEnvItem) => (
      <Space size={4} wrap>
        <Button type="link" size="small" icon={<EyeOutlined />} onClick={() => actions.onViewPackages(record)}>
          依赖
        </Button>
        <Button type="link" size="small" icon={<EditOutlined />} onClick={() => actions.onEdit(record)}>
          编辑
        </Button>
        <Button type="link" size="small" icon={<DownloadOutlined />} onClick={() => actions.onInstall(record)}>
          安装
        </Button>
        <Button type="link" size="small" danger icon={<DeleteOutlined />} onClick={() => actions.onDelete(record)}>
          删除
        </Button>
      </Space>
    ),
  },
]
