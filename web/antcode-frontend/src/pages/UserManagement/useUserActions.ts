import { useCallback } from 'react'
import { globalMessage } from '@/hooks/useMessage'
import type { User } from '@/types'
import { getErrorMessage } from '@/utils/helpers'
import { userManagementApi } from './api'
import type { UserCreateValues, UserEditValues } from './types'

interface UserActionOptions {
  reload: () => Promise<void> | void
  markOffline: (userId: string) => void
  clearSelection: () => void
}

const showFailure = (error: unknown) => globalMessage.error(getErrorMessage(error))

export const useUserActions = ({ reload, markOffline, clearSelection }: UserActionOptions) => {
  const createUser = useCallback(async (values: UserCreateValues) => {
    try {
      await userManagementApi.create(values)
      globalMessage.success('用户已创建')
      await reload()
    } catch (error) {
      showFailure(error)
      throw error
    }
  }, [reload])

  const updateUser = useCallback(async (target: User, values: UserEditValues) => {
    try {
      await userManagementApi.updateProfile(target.id, values)
      if (target.is_admin !== values.is_admin) await userManagementApi.updateRole(target, values.is_admin)
      globalMessage.success('用户已更新')
      await reload()
    } catch (error) {
      showFailure(error)
      await reload()
      throw error
    }
  }, [reload])

  const resetPassword = useCallback(async (target: User, password: string) => {
    try {
      await userManagementApi.resetPassword(target.id, password)
      globalMessage.success('密码已重置，目标用户的会话已撤销')
    } catch (error) {
      showFailure(error)
      throw error
    }
  }, [])

  const kickUser = useCallback(async (target: User) => {
    try {
      const result = await userManagementApi.revokeSessions(target.id)
      markOffline(target.id)
      globalMessage.success(`已撤销 ${result.revoked_sessions} 个活跃会话`)
    } catch (error) {
      showFailure(error)
      throw error
    }
  }, [markOffline])

  const deleteUser = useCallback(async (target: User) => {
    try {
      await userManagementApi.delete(target.id)
      globalMessage.success(`用户 ${target.username} 已删除`)
      await reload()
    } catch (error) {
      showFailure(error)
      throw error
    }
  }, [reload])

  const batchDelete = useCallback(async (targets: User[]) => {
    const results = await Promise.allSettled(targets.map((user) => userManagementApi.delete(user.id)))
    const failures = results.filter((result) => result.status === 'rejected') as PromiseRejectedResult[]
    if (failures.length) globalMessage.error(`有 ${failures.length} 个用户删除失败：${getErrorMessage(failures[0].reason)}`)
    const deleted = results.length - failures.length
    if (deleted) globalMessage.success(`已删除 ${deleted} 个用户`)
    clearSelection()
    await reload()
  }, [clearSelection, reload])

  return { createUser, updateUser, resetPassword, kickUser, deleteUser, batchDelete }
}
