import { useCallback, useEffect, useMemo, useReducer } from 'react'
import { message } from 'antd'
import { getErrorMessage } from '@/utils/helpers'
import { userManagementApi } from './api'
import type { UserListQuery } from './types'
import {
  INITIAL_PAGE,
  INITIAL_PAGE_SIZE,
  initialUserListState,
  nextSort,
  userListReducer,
} from './userListState'

const initialQuery: UserListQuery = {
  page: INITIAL_PAGE,
  size: INITIAL_PAGE_SIZE,
  sortField: null,
  sortOrder: 'asc',
}

export const useUserList = (enabled: boolean) => {
  const [state, dispatch] = useReducer(userListReducer, initialUserListState)
  const load = useCallback(async (query: UserListQuery) => {
    if (!enabled) return
    dispatch({ type: 'loading', value: true })
    dispatch({ type: 'query', page: query.page, pageSize: query.size, sortField: query.sortField, sortOrder: query.sortOrder })
    try {
      const data = await userManagementApi.list(query)
      dispatch({
        type: 'loaded',
        users: data.items,
        pagination: { current: data.pagination.page, pageSize: data.pagination.size, total: data.pagination.total },
      })
    } catch (error) {
      dispatch({ type: 'loading', value: false })
      message.error(getErrorMessage(error))
    }
  }, [enabled])

  useEffect(() => { void load(initialQuery) }, [load])
  const refresh = useCallback(() => load({
    page: state.pagination.current,
    size: state.pagination.pageSize,
    sortField: state.sortField,
    sortOrder: state.sortOrder,
  }), [load, state.pagination, state.sortField, state.sortOrder])
  const changePage = (page: number, size: number) => load({ page, size, sortField: state.sortField, sortOrder: state.sortOrder })
  const changeSort = (field: 'id' | 'username' | 'created_at') => {
    const [sortField, sortOrder] = nextSort(state.sortField, state.sortOrder, field)
    void load({ page: INITIAL_PAGE, size: state.pagination.pageSize, sortField, sortOrder })
  }
  const users = useMemo(() => {
    const keyword = state.searchKeyword.trim().toLowerCase()
    if (!keyword) return state.users
    return state.users.filter((user) => String(user.id).includes(keyword) || user.username.toLowerCase().includes(keyword))
  }, [state.searchKeyword, state.users])

  return { state, users, load, refresh, changePage, changeSort, dispatch }
}
