import { create } from 'zustand'
import type { User } from '@/types'

interface AuthStore {
  // 状态
  user: User | null
  isAuthenticated: boolean
  isLoading: boolean
  error: string | null
  /**
   * 后端 `GET /api/v1/auth/permissions` 返回的角色派生权限集合（权威来源）。
   * 当前仅作为会话有效性的 fail-closed 探针留存：拉取失败即中断登录/会话恢复。
   * 界面级鉴权的唯一真相源是 `user.is_admin` / `user.role`
   * （见 AdminRoute / SuperAdminRoute），不要在组件里改用本字段做细粒度判断，
   * 除非后端先给路由挂上 `require_permission` 依赖。
   */
  permissions: string[]
  /**
   * 认证纪元：每次登录/登出（setUser/clearUser）单调递增。
   * 用于让慢速的后台会话检查在完成时判断期间是否发生过登录/登出，
   * 避免过期的 401 结果覆盖新建立的会话。
   */
  authEpoch: number

  // Actions
  setUser: (user: User) => void
  setLoading: (loading: boolean) => void
  setError: (error: string | null) => void
  setPermissions: (permissions: string[]) => void
  clearUser: () => void
  updateUser: (updates: Partial<User>) => void
}

export const useAuthStore = create<AuthStore>()((set, get) => ({
      // 初始状态
      user: null,
      isAuthenticated: false,
      isLoading: false,
      error: null,
      permissions: [],
      authEpoch: 0,

      // 设置用户信息
      setUser: (user: User) => {
        set((state) => ({
          user,
          isAuthenticated: true,
          error: null,
          authEpoch: state.authEpoch + 1
        }))
      },

      // 设置加载状态
      setLoading: (loading: boolean) => {
        set({ isLoading: loading })
      },

      // 设置错误信息
      setError: (error: string | null) => {
        set({ error })
      },

      // 设置权限
      setPermissions: (permissions: string[]) => {
        set({ permissions })
      },

      // 清除用户信息
      clearUser: () => {
        set((state) => ({
          user: null,
          isAuthenticated: false,
          error: null,
          permissions: [],
          authEpoch: state.authEpoch + 1
        }))
      },

      // 更新用户信息
      updateUser: (updates: Partial<User>) => {
        const { user } = get()
        if (user) {
          set({
            user: { ...user, ...updates }
          })
        }
      }
    }))

// Hook 函数
export const useAuth = () => {
  const store = useAuthStore()
  return {
    user: store.user,
    isAuthenticated: store.isAuthenticated,
    isLoading: store.isLoading,
    error: store.error,
    permissions: store.permissions,
    setUser: store.setUser,
    setLoading: store.setLoading,
    setError: store.setError,
    setPermissions: store.setPermissions,
    clearUser: store.clearUser,
    updateUser: store.updateUser
  }
}

export default useAuthStore
