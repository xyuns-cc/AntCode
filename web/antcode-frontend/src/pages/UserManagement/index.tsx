import { useCallback, useMemo, useState } from 'react'
import { Button, Input, Modal, Result, Space } from 'antd'
import { DeleteOutlined, PlusOutlined, ReloadOutlined, SearchOutlined, TeamOutlined } from '@ant-design/icons'
import PageContainer from '@/components/common/PageContainer'
import FilterBar from '@/components/common/FilterBar'
import { useAuth } from '@/hooks/useAuth'
import type { User } from '@/types'
import { CreateUserModal } from './CreateUserModal'
import { EditUserModal } from './EditUserModal'
import { PasswordResetModal } from './PasswordResetModal'
import { UserTable } from './UserTable'
import { useUserActions } from './useUserActions'
import { useUserList } from './useUserList'

type Dialog = 'create' | 'edit' | 'password' | null

const UserToolbar = ({ keyword, loading, selected, canDelete, onKeyword, onRefresh, onBatchDelete, onCreate }: {
  keyword: string; loading: boolean; selected: number; canDelete: boolean
  onKeyword: (value: string) => void; onRefresh: () => void; onBatchDelete: () => void; onCreate: () => void
}) => (
  <FilterBar
    filters={<Input placeholder="搜索 ID 或用户名" prefix={<SearchOutlined />} allowClear value={keyword} onChange={(event) => onKeyword(event.target.value)} style={{ width: 240 }} />}
    actions={<>
      <Button icon={<ReloadOutlined />} onClick={onRefresh} loading={loading}>刷新</Button>
      {canDelete && <Button danger icon={<DeleteOutlined />} disabled={!selected} onClick={onBatchDelete}>批量删除{selected ? ` (${selected})` : ''}</Button>}
      <Button type="primary" icon={<PlusOutlined />} onClick={onCreate}>添加用户</Button>
    </>}
  />
)

const UserDialogs = ({ dialog, target, currentUser, close, actions }: {
  dialog: Dialog; target: User | null; currentUser: User; close: () => void
  actions: ReturnType<typeof useUserActions>
}) => {
  const canChangeRole = currentUser.role === 'super_admin' && target?.role !== 'super_admin' && String(target?.id) !== String(currentUser.id)
  return <>
    <CreateUserModal open={dialog === 'create'} canCreateAdmin={currentUser.role === 'super_admin'} onCancel={close} onSubmit={actions.createUser} />
    <EditUserModal open={dialog === 'edit'} target={target} canChangeRole={canChangeRole} onCancel={close} onSubmit={actions.updateUser} />
    <PasswordResetModal open={dialog === 'password'} target={target} onCancel={close} onSubmit={actions.resetPassword} />
  </>
}

const UserManagement = () => {
  const { user: currentUser } = useAuth()
  const list = useUserList(Boolean(currentUser?.is_admin))
  const { dispatch } = list
  const [dialog, setDialog] = useState<Dialog>(null)
  const [target, setTarget] = useState<User | null>(null)
  const closeDialog = () => { setDialog(null); setTarget(null) }
  const markOffline = useCallback((userId: string) => dispatch({ type: 'offline', userId }), [dispatch])
  const clearSelection = useCallback(() => dispatch({ type: 'select', value: [] }), [dispatch])
  const actions = useUserActions({ reload: list.refresh, markOffline, clearSelection })
  const openFor = (nextDialog: Exclude<Dialog, 'create' | null>, user: User) => { setTarget(user); setDialog(nextDialog) }
  const tableActions = useMemo(() => ({
    edit: (user: User) => openFor('edit', user),
    resetPassword: (user: User) => openFor('password', user),
    kick: actions.kickUser,
    delete: actions.deleteUser,
  }), [actions.deleteUser, actions.kickUser])
  if (!currentUser?.is_admin) return <Result status="403" title="权限不足" subTitle="只有管理员才能访问用户管理页面" />

  const batchTargets = list.state.users.filter((user) => list.state.selectedRowKeys.includes(user.id))
  const confirmBatchDelete = () => Modal.confirm({
    title: '确认批量删除', content: `确定删除选中的 ${batchTargets.length} 个用户？`, okText: '确认删除', okType: 'danger', cancelText: '取消',
    onOk: () => actions.batchDelete(batchTargets),
  })
  return (
    <PageContainer title={<Space><TeamOutlined /><span>用户管理</span></Space>} toolbar={
      <UserToolbar keyword={list.state.searchKeyword} loading={list.state.loading} selected={list.state.selectedRowKeys.length} canDelete={currentUser.role === 'super_admin'} onKeyword={(value) => list.dispatch({ type: 'search', value })} onRefresh={() => void list.refresh()} onBatchDelete={confirmBatchDelete} onCreate={() => setDialog('create')} />
    }>
      <UserTable currentUser={currentUser} users={list.users} loading={list.state.loading} pagination={list.state.pagination} sortField={list.state.sortField} sortOrder={list.state.sortOrder} actions={tableActions} selection={{ selectedRowKeys: list.state.selectedRowKeys, setSelectedRowKeys: (value) => list.dispatch({ type: 'select', value }) }} onSort={list.changeSort} onPageChange={(page, size) => void list.changePage(page, size)} />
      <UserDialogs dialog={dialog} target={target} currentUser={currentUser} close={closeDialog} actions={actions} />
    </PageContainer>
  )
}

export default UserManagement
