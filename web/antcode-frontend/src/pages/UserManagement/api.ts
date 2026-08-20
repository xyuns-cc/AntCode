import apiClient from '@/services/api'
import { encryptPasswords, withStaleKeyRecovery } from '@/utils/loginEncryption'
import type { ApiResponse, PaginationResponse, User } from '@/types'
import type { UserCreateValues, UserEditValues, UserListQuery } from './types'

interface SessionRevokeResult {
  revoked_sessions: number
}

const requireSuccess = <T>(response: ApiResponse<T>): T => {
  if (!response.success) {
    throw new Error(response.message || '请求失败')
  }
  return response.data
}

export const userManagementApi = {
  async list(query: UserListQuery): Promise<PaginationResponse<User>['data']> {
    const response = await apiClient.get<PaginationResponse<User>>('/api/v1/users/', {
      params: {
        page: query.page,
        size: query.size,
        search: query.search || undefined,
        sort_by: query.sortField ?? undefined,
        sort_order: query.sortField ? query.sortOrder : undefined,
      },
    })
    return requireSuccess(response.data)
  },

  async create(values: UserCreateValues): Promise<User> {
    const role = values.is_admin ? 'admin' : 'user'
    // 解构掉 password 而不是覆盖它：`...values` 会把明文原样带上线，
    // 建号的初始口令与登录/改密/重置是同一类机密，走同一把公钥。
    const { password, ...profile } = values
    const { encrypted, algorithm, keyId } = await encryptPasswords([password])
    const response = await withStaleKeyRecovery(() => apiClient.post<ApiResponse<User>>('/api/v1/users/', {
      ...profile,
      role,
      encrypted_password: encrypted[0],
      encryption: algorithm,
      key_id: keyId,
    }))
    return requireSuccess(response.data)
  },

  async updateProfile(userId: string, values: UserEditValues): Promise<User> {
    const response = await apiClient.put<ApiResponse<User>>(`/api/v1/users/${userId}`, {
      username: values.username,
      email: values.email,
      is_active: values.is_active,
    })
    return requireSuccess(response.data)
  },

  async updateRole(user: User, isAdmin: boolean): Promise<User> {
    const oldRole = user.role ?? (user.is_admin ? 'admin' : 'user')
    const response = await apiClient.post<ApiResponse<User>>(`/api/v1/users/${user.id}/role`, {
      old_role: oldRole,
      new_role: isAdmin ? 'admin' : 'user',
    })
    return requireSuccess(response.data)
  },

  async resetPassword(userId: string, password: string): Promise<void> {
    const { encrypted, algorithm, keyId } = await encryptPasswords([password])
    const response = await withStaleKeyRecovery(() => apiClient.put<ApiResponse<null>>(
      `/api/v1/users/${userId}/reset-password`,
      {
        encrypted_new_password: encrypted[0],
        encryption: algorithm,
        key_id: keyId,
      }
    ))
    requireSuccess(response.data)
  },

  async revokeSessions(userId: string): Promise<SessionRevokeResult> {
    const response = await apiClient.post<ApiResponse<SessionRevokeResult>>(
      `/api/v1/users/${userId}/kick`
    )
    return requireSuccess(response.data)
  },

  async delete(userId: string): Promise<void> {
    const response = await apiClient.delete<ApiResponse<null>>(`/api/v1/users/${userId}`)
    requireSuccess(response.data)
  },
}
