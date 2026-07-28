import type React from 'react'
import type { User } from '@/types'
import type { PaginationState, SortField, SortOrder } from './types'

export const INITIAL_PAGE = 1
export const INITIAL_PAGE_SIZE = 20

export interface UserListState {
  users: User[]
  loading: boolean
  pagination: PaginationState
  sortField: SortField
  sortOrder: SortOrder
  searchKeyword: string
  selectedRowKeys: React.Key[]
}

export type UserListAction =
  | { type: 'loading'; value: boolean }
  | { type: 'loaded'; users: User[]; pagination: PaginationState }
  | { type: 'query'; page: number; pageSize: number; sortField: SortField; sortOrder: SortOrder }
  | { type: 'search'; value: string }
  | { type: 'select'; value: React.Key[] }
  | { type: 'offline'; userId: string }

export const initialUserListState: UserListState = {
  users: [],
  loading: false,
  pagination: { current: INITIAL_PAGE, pageSize: INITIAL_PAGE_SIZE, total: 0 },
  sortField: null,
  sortOrder: 'asc',
  searchKeyword: '',
  selectedRowKeys: [],
}

export const userListReducer = (state: UserListState, action: UserListAction): UserListState => {
  if (action.type === 'loading') return { ...state, loading: action.value }
  if (action.type === 'loaded') {
    return { ...state, loading: false, users: action.users, pagination: action.pagination }
  }
  if (action.type === 'query') {
    const pagination = { ...state.pagination, current: action.page, pageSize: action.pageSize }
    return { ...state, pagination, sortField: action.sortField, sortOrder: action.sortOrder }
  }
  if (action.type === 'search') return { ...state, searchKeyword: action.value }
  if (action.type === 'select') return { ...state, selectedRowKeys: action.value }
  const users = state.users.map((user) => user.id === action.userId ? { ...user, is_online: false } : user)
  return { ...state, users }
}

export const nextSort = (
  currentField: SortField,
  currentOrder: SortOrder,
  field: Exclude<SortField, null>,
): [SortField, SortOrder] => {
  if (currentField !== field) return [field, 'asc']
  if (currentOrder === 'asc') return [field, 'desc']
  return [null, 'asc']
}
