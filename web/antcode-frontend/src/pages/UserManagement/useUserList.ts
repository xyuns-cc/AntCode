import { useCallback, useEffect, useReducer, useRef } from 'react'
import { globalMessage } from '@/hooks/useMessage'
import { getErrorMessage } from '@/utils/helpers'
import { userManagementApi } from './api'
import type { UserListQuery } from './types'
import { INITIAL_PAGE, initialUserListState, nextSort, userListReducer } from './userListState'

const SEARCH_DEBOUNCE_MS = 300

export const useUserList = (enabled: boolean) => {
  const [state, dispatch] = useReducer(userListReducer, initialUserListState)
  const requestGeneration = useRef(0)
  const listPreferences = useRef({
    pageSize: state.pagination.pageSize,
    sortField: state.sortField,
    sortOrder: state.sortOrder,
  })
  listPreferences.current = {
    pageSize: state.pagination.pageSize,
    sortField: state.sortField,
    sortOrder: state.sortOrder,
  }
  const load = useCallback(
    async (query: UserListQuery) => {
      if (!enabled) return
      const generation = ++requestGeneration.current
      dispatch({ type: 'loading', value: true })
      dispatch({
        type: 'query',
        page: query.page,
        pageSize: query.size,
        sortField: query.sortField,
        sortOrder: query.sortOrder,
      })
      try {
        const data = await userManagementApi.list(query)
        if (generation !== requestGeneration.current) return
        dispatch({
          type: 'loaded',
          users: data.items,
          pagination: {
            current: data.pagination.page,
            pageSize: data.pagination.size,
            total: data.pagination.total,
          },
        })
      } catch (error) {
        if (generation !== requestGeneration.current) return
        dispatch({ type: 'loading', value: false })
        globalMessage.error(getErrorMessage(error))
      }
    },
    [enabled]
  )

  useEffect(() => {
    if (!enabled) requestGeneration.current += 1
  }, [enabled])

  useEffect(() => {
    const search = state.searchKeyword.trim()
    const timer = window.setTimeout(() => {
      void load({
        page: INITIAL_PAGE,
        size: listPreferences.current.pageSize,
        search: search || undefined,
        sortField: listPreferences.current.sortField,
        sortOrder: listPreferences.current.sortOrder,
      })
    }, SEARCH_DEBOUNCE_MS)
    return () => window.clearTimeout(timer)
  }, [load, state.searchKeyword])
  const refresh = useCallback(
    () =>
      load({
        page: state.pagination.current,
        size: state.pagination.pageSize,
        search: state.searchKeyword.trim() || undefined,
        sortField: state.sortField,
        sortOrder: state.sortOrder,
      }),
    [load, state.pagination, state.searchKeyword, state.sortField, state.sortOrder]
  )
  const changePage = (page: number, size: number) =>
    load({
      page,
      size,
      search: state.searchKeyword.trim() || undefined,
      sortField: state.sortField,
      sortOrder: state.sortOrder,
    })
  const changeSort = (field: 'id' | 'username' | 'created_at') => {
    const [sortField, sortOrder] = nextSort(state.sortField, state.sortOrder, field)
    void load({
      page: INITIAL_PAGE,
      size: state.pagination.pageSize,
      search: state.searchKeyword.trim() || undefined,
      sortField,
      sortOrder,
    })
  }

  return { state, users: state.users, load, refresh, changePage, changeSort, dispatch }
}
