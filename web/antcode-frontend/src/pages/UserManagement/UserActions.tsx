import { Button, Popconfirm, Space } from 'antd'
import { DeleteOutlined, EditOutlined, KeyOutlined, LogoutOutlined } from '@ant-design/icons'
import type { User } from '@/types'
import type { UserTableActions } from './types'

interface Props {
  currentUser: User
  target: User
  actions: UserTableActions
}

const isSameUser = (left: User, right: User) => String(left.id) === String(right.id)

export const UserActions = ({ currentUser, target, actions }: Props) => {
  const isSelf = isSameUser(currentUser, target)
  const isSuperAdmin = currentUser.role === 'super_admin'
  const protectedTarget = target.role === 'super_admin'
  const canEdit = isSelf || isSuperAdmin || !target.is_admin
  const canManageSessions = isSuperAdmin && !isSelf && !protectedTarget
  return (
    <Space size={4} wrap>
      {canEdit && <Button type="link" size="small" icon={<EditOutlined />} onClick={() => actions.edit(target)}>编辑</Button>}
      {canManageSessions && <Button type="link" size="small" icon={<KeyOutlined />} onClick={() => actions.resetPassword(target)}>改密</Button>}
      {canManageSessions && (
        <Popconfirm title="确认强制下线" description={`撤销用户“${target.username}”的全部登录会话？`} onConfirm={() => actions.kick(target)} okText="确定" cancelText="取消">
          <Button type="link" size="small" danger icon={<LogoutOutlined />}>踢下线</Button>
        </Popconfirm>
      )}
      {canManageSessions && (
        <Popconfirm title="确认删除" description={`确定要删除用户“${target.username}”吗？此操作不可恢复。`} onConfirm={() => actions.delete(target)} okText="确定" cancelText="取消">
          <Button type="link" size="small" danger icon={<DeleteOutlined />}>删除</Button>
        </Popconfirm>
      )}
    </Space>
  )
}
