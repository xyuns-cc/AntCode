import type React from 'react'
import type { User } from '@/types'

export type SortField = 'id' | 'username' | 'created_at' | null
export type SortOrder = 'asc' | 'desc'

export interface UserCreateValues {
  username: string
  password: string
  email?: string
  is_active: boolean
  is_admin: boolean
}

export interface UserEditValues {
  username: string
  email?: string
  is_active: boolean
  is_admin: boolean
}

export interface PasswordResetValues {
  new_password: string
  confirm_password: string
}

export interface PaginationState {
  current: number
  pageSize: number
  total: number
}

export interface UserListQuery {
  page: number
  size: number
  search?: string
  sortField: SortField
  sortOrder: SortOrder
}

export interface UserTableActions {
  edit: (user: User) => void
  resetPassword: (user: User) => void
  kick: (user: User) => Promise<void>
  delete: (user: User) => Promise<void>
}

export interface UserTableSelection {
  selectedRowKeys: React.Key[]
  setSelectedRowKeys: (keys: React.Key[]) => void
}
