import { Space, Tag, Tooltip } from 'antd'
import { TeamOutlined, UserOutlined } from '@ant-design/icons'
import type { ColumnsType } from 'antd/es/table'
import type { User } from '@/types'
import type { SortField, SortOrder, UserTableActions } from './types'
import { UserActions } from './UserActions'

interface ColumnOptions {
  currentUser: User
  actions: UserTableActions
  sortField: SortField
  sortOrder: SortOrder
  onSort: (field: 'id' | 'username' | 'created_at') => void
}

const sortTitle = (label: string, field: Exclude<SortField, null>, options: ColumnOptions) => (
  <button type="button" className="ant-btn ant-btn-link ant-btn-sm" onClick={(event) => { event.stopPropagation(); options.onSort(field) }}>
    {label} {options.sortField === field && (options.sortOrder === 'asc' ? '↑' : '↓')}
  </button>
)

const identityColumns = (options: ColumnOptions): ColumnsType<User> => [
  { title: sortTitle('ID', 'id', options), dataIndex: 'id', key: 'id', width: 100 },
  {
    title: sortTitle('用户名', 'username', options), dataIndex: 'username', key: 'username', width: 190,
    render: (text: string, user: User) => (
      <Tooltip title={text}><Space><UserOutlined /><span>{text}</span>{user.is_admin && <Tag color={user.role === 'super_admin' ? 'red' : 'gold'} icon={<TeamOutlined />}>{user.role === 'super_admin' ? '超级管理员' : '管理员'}</Tag>}</Space></Tooltip>
    ),
  },
  { title: '邮箱', dataIndex: 'email', key: 'email', width: 210, render: (email?: string) => email || '-' },
]

const statusColumns: ColumnsType<User> = [
  { title: '状态', dataIndex: 'is_active', key: 'is_active', width: 90, render: (active: boolean) => <Tag color={active ? 'success' : 'error'}>{active ? '激活' : '禁用'}</Tag> },
  { title: '在线', dataIndex: 'is_online', key: 'is_online', width: 90, render: (online: boolean) => <Tag color={online ? 'success' : 'default'}>{online ? '在线' : '离线'}</Tag> },
]

const timeColumns = (options: ColumnOptions): ColumnsType<User> => [
  { title: sortTitle('创建时间', 'created_at', options), dataIndex: 'created_at', key: 'created_at', width: 180, render: (date: string) => new Date(date).toLocaleString() },
  { title: '最后登录', dataIndex: 'last_login_at', key: 'last_login_at', width: 180, render: (date?: string) => date ? new Date(date).toLocaleString() : '从未登录' },
]

export const buildUserColumns = (options: ColumnOptions): ColumnsType<User> => [
  ...identityColumns(options),
  ...statusColumns,
  ...timeColumns(options),
  { title: '操作', key: 'actions', width: 300, fixed: 'right', render: (_, target) => <UserActions currentUser={options.currentUser} target={target} actions={options.actions} /> },
]
