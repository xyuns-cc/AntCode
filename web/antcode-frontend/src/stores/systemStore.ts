/**
 * 系统信息 Store
 */
import { create } from 'zustand'
import { getAppInfo, type AppInfo } from '@/services/system'

interface SystemState {
  appInfo: AppInfo | null
  isLoading: boolean
  fetchAppInfo: () => Promise<void>
}

// 默认应用信息（后端不可用时的回退值）
const DEFAULT_APP_INFO: AppInfo = {
  name: 'AntCode',
  title: 'AntCode 任务调度平台',
  version: '0.0.0',
  description: '',
  copyright_year: '2025',
}

export const useSystemStore = create<SystemState>((set, get) => ({
  appInfo: null,
  isLoading: false,

  fetchAppInfo: async () => {
    // 已有信息则不重复请求
    if (get().appInfo || get().isLoading) return

    set({ isLoading: true })
    try {
      const info = await getAppInfo()
      set({ appInfo: info })
    } catch {
      // 失败时使用默认值
      set({ appInfo: DEFAULT_APP_INFO })
    } finally {
      set({ isLoading: false })
    }
  },
}))
