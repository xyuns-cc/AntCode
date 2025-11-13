import { create } from 'zustand'
import { persist } from 'zustand/middleware'
import type { User } from '@/types'

interface AuthStore {
  // 状态
  user: User | null
  isAuthenticated: boolean
  isLoading: boolean
  error: string | null
  permissions: string[]

  // Actions
  setUser: (user: User) => void
  setLoading: (loading: boolean) => void
  setError: (error: string | null) => void
  setPermissions: (permissions: string[]) => void
  clearUser: () => void
  updateUser: (updates: Partial<User>) => void
  
  // 权限检查
  hasPermission: (permission: string) => boolean
  hasAnyPermission: (permissions: string[]) => boolean
  hasAllPermissions: (permissions: string[]) => boolean
}

export const useAuthStore = create<AuthStore>()(
  persist(
    (set, get) => ({
      // 初始状态
      user: null,
      isAuthenticated: false,
      isLoading: false,
      error: null,
      permissions: [],

      // 设置用户信息
      setUser: (user: User) => {
        set({
          user,
          isAuthenticated: true,
          error: null
        })
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
        set({
          user: null,
          isAuthenticated: false,
          error: null,
          permissions: []
        })
      },

      // 更新用户信息
      updateUser: (updates: Partial<User>) => {
        const { user } = get()
        if (user) {
          set({
            user: { ...user, ...updates }
          })
        }
      },

      // 检查是否有特定权限
      hasPermission: (permission: string) => {
        const { permissions } = get()
        return permissions.includes(permission) || permissions.includes('admin')
      },

      // 检查是否有任意一个权限
      hasAnyPermission: (requiredPermissions: string[]) => {
        const { permissions } = get()
        return requiredPermissions.some(permission => 
          permissions.includes(permission) || permissions.includes('admin')
        )
      },

      // 检查是否有所有权限
      hasAllPermissions: (requiredPermissions: string[]) => {
        const { permissions } = get()
        if (permissions.includes('admin')) return true
        return requiredPermissions.every(permission => permissions.includes(permission))
      }
    }),
    {
      name: 'auth-storage',
      partialize: (state) => ({
        user: state.user,
        isAuthenticated: state.isAuthenticated,
        permissions: state.permissions
      }),
    }
  )
)

// 选择器函数
export const selectUser = (state: AuthStore) => state.user
export const selectIsAuthenticated = (state: AuthStore) => state.isAuthenticated
export const selectIsLoading = (state: AuthStore) => state.isLoading
export const selectError = (state: AuthStore) => state.error
export const selectPermissions = (state: AuthStore) => state.permissions

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
    updateUser: store.updateUser,
    hasPermission: store.hasPermission,
    hasAnyPermission: store.hasAnyPermission,
    hasAllPermissions: store.hasAllPermissions
  }
}

export default useAuthStore
