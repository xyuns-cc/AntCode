import { useMemo } from 'react'
import ResponsiveTable from '@/components/common/ResponsiveTable'
import type { User } from '@/types'
import type { PaginationState, SortField, SortOrder, UserTableActions, UserTableSelection } from './types'
import { buildUserColumns } from './userColumns'

interface Props {
  currentUser: User
  users: User[]
  loading: boolean
  pagination: PaginationState
  sortField: SortField
  sortOrder: SortOrder
  actions: UserTableActions
  selection: UserTableSelection
  onSort: (field: 'id' | 'username' | 'created_at') => void
  onPageChange: (page: number, size: number) => void
}

export const UserTable = (props: Props) => {
  const columns = useMemo(() => buildUserColumns({ currentUser: props.currentUser, actions: props.actions, sortField: props.sortField, sortOrder: props.sortOrder, onSort: props.onSort }), [props.actions, props.currentUser, props.onSort, props.sortField, props.sortOrder])
  const selectable = props.currentUser.role === 'super_admin'
  const checkbox = (user: User) => ({
    disabled: !selectable || user.is_admin || String(user.id) === String(props.currentUser.id),
    title: !selectable ? '仅超级管理员可删除用户' : undefined,
  })
  return (
    <ResponsiveTable<User>
      fill columns={columns} dataSource={props.users} rowKey="id" loading={props.loading}
      rowSelection={{ selectedRowKeys: props.selection.selectedRowKeys, onChange: props.selection.setSelectedRowKeys, getCheckboxProps: checkbox }}
      pagination={{ current: props.pagination.current, pageSize: props.pagination.pageSize, total: props.pagination.total, onChange: props.onPageChange, onShowSizeChange: (_current, size) => props.onPageChange(1, size) }}
    />
  )
}
