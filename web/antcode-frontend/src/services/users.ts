/**
 * 用户服务
 * 负责用户相关的所有API调用
 */

import { BaseService } from './base'
import type { User, ApiResponse, PaginatedResponse } from '@/types'

export interface SimpleUser {
  id: number
  username: string
}

export interface UserListParams {
  page?: number
  size?: number
  search?: string
  is_active?: boolean
  is_admin?: boolean
}

class UserService extends BaseService {
  constructor() {
    super('/api/v1/users')
  }

  /**
   * 获取简化用户列表（用于下拉选择）
   */
  async getSimpleUserList(): Promise<SimpleUser[]> {
    const data = await this.get<ApiResponse<SimpleUser[]>>('/simple')
    return data || []
  }

  /**
   * 获取完整用户列表（分页）
   */
  async getUserList(params: UserListParams = {}): Promise<{ users: User[]; total: number }> {
    const { page = 1, size = 20, ...filters } = params
    const data = await this.get<ApiResponse<User[]>>('/', {
      params: { page, size, ...filters },
    })

    return {
      users: data || [],
      total: data?.length || 0,
    }
  }

  /**
   * 获取用户详情
   */
  async getUser(id: number): Promise<User> {
    return this.get<User>(`/${id}`)
  }

  /**
   * 创建用户
   */
  async createUser(userData: Partial<User>): Promise<User> {
    return this.post<User>('/', userData)
  }

  /**
   * 更新用户
   */
  async updateUser(id: number, userData: Partial<User>): Promise<User> {
    return this.put<User>(`/${id}`, userData)
  }

  /**
   * 删除用户
   */
  async deleteUser(id: number): Promise<void> {
    return this.delete(`/${id}`)
  }

  /**
   * 重置用户密码
   */
  async resetPassword(id: number, newPassword: string): Promise<void> {
    return this.post(`/${id}/reset-password`, { new_password: newPassword })
  }

  /**
   * 修改密码
   */
  async changePassword(oldPassword: string, newPassword: string): Promise<void> {
    return this.post('/change-password', {
      old_password: oldPassword,
      new_password: newPassword,
    })
  }

  /**
   * 批量操作
   */
  async batchUpdateStatus(userIds: number[], isActive: boolean): Promise<void> {
    return this.post('/batch/status', {
      user_ids: userIds,
      is_active: isActive,
    })
  }

  async batchDelete(userIds: number[]): Promise<void> {
    return this.post('/batch/delete', {
      user_ids: userIds,
    })
  }
}

export const userService = new UserService()
export default userService
