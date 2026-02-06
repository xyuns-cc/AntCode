/**
 * Worker 状态管理
 * 管理当前选中的 Worker 和 Worker 列表
 */
import { create } from 'zustand'
import { persist } from 'zustand/middleware'
import type { Worker } from '@/types'
import { workerService } from '@/services/workers'
import { isAbortError } from '@/utils/helpers'

interface WorkerState {
  // 当前选中的 Worker，undefined 表示"全部 Worker"
  currentWorker: Worker | undefined
  // 所有 Worker 列表
  workers: Worker[]
  // 加载状态
  loading: boolean
  // 错误信息
  error: string
  // 最后刷新时间
  lastRefreshed: number
}

interface WorkerActions {
  // 设置当前 Worker
  setCurrentWorker: (worker: Worker | undefined) => void
  // 设置 Worker 列表
  setWorkers: (workers: Worker[]) => void
  // 刷新 Worker 列表（显示loading）
  refreshWorkers: () => Promise<void>
  // 静默刷新 Worker 列表（不显示loading，用于后台自动更新）
  silentRefresh: () => Promise<void>
  // 添加 Worker
  addWorker: (worker: Worker) => void
  // 更新 Worker
  updateWorker: (workerId: string, updates: Partial<Worker>) => void
  // 删除 Worker
  removeWorker: (workerId: string) => void
  // 清除错误
  clearError: () => void
  // 从 URL 同步 Worker
  syncFromUrl: () => void
  // 同步到 URL
  syncToUrl: () => void
}

type WorkerStore = WorkerState & WorkerActions

// 从 URL 获取 Worker ID
const getWorkerIdFromUrl = (): string => {
  if (typeof window === 'undefined') return ''
  const params = new URLSearchParams(window.location.search)
  return params.get('worker') || ''
}

// 更新 URL 中的 Worker 参数
const updateUrlWorkerParam = (workerId: string) => {
  if (typeof window === 'undefined') return
  const url = new URL(window.location.href)
  if (workerId) {
    url.searchParams.set('worker', workerId)
  } else {
    url.searchParams.delete('worker')
  }
  window.history.replaceState({}, '', url.toString())
}

let refreshController: AbortController | null = null
let silentController: AbortController | null = null

export const useWorkerStore = create<WorkerStore>()(
  persist(
    (set, get) => ({
      // 初始状态
      currentWorker: undefined,
      workers: [],
      loading: false,
      error: '',
      lastRefreshed: 0,

      // 设置当前 Worker
      setCurrentWorker: (worker) => {
        set({ currentWorker: worker })
        updateUrlWorkerParam(worker?.id || '')
      },

      // 设置 Worker 列表
      setWorkers: (workers) => {
        set({ workers, lastRefreshed: Date.now() })
      },

      // 刷新 Worker 列表（显示loading状态）
      refreshWorkers: async () => {
        set({ loading: true, error: '' })
        if (refreshController) {
          refreshController.abort()
        }
        const controller = new AbortController()
        refreshController = controller
        try {
          const workers = await workerService.getAllWorkers({ signal: controller.signal })
          if (refreshController !== controller || controller.signal.aborted) return
          set({ workers, loading: false, lastRefreshed: Date.now() })

          // 如果当前选中的 Worker 已不存在，重置为全局视图
          const { currentWorker } = get()
          if (currentWorker && !workers.find((w) => w.id === currentWorker.id)) {
            set({ currentWorker: undefined })
            updateUrlWorkerParam('')
          }
        } catch (error: unknown) {
          if (refreshController !== controller || isAbortError(error)) return
          const message = error instanceof Error ? error.message : '获取 Worker 列表失败'
          set({
            loading: false,
            error: message
          })
        } finally {
          if (refreshController === controller) {
            refreshController = null
          }
        }
      },

      // 静默刷新 Worker 列表（不显示loading，用于后台自动更新）
      silentRefresh: async () => {
        if (silentController) {
          silentController.abort()
        }
        const controller = new AbortController()
        silentController = controller
        try {
          const newWorkers = await workerService.getAllWorkers({ signal: controller.signal })
          if (silentController !== controller || controller.signal.aborted) return
          const { workers: oldWorkers, currentWorker } = get()

          // 只有当数据真正变化时才更新（避免不必要的重渲染）
          const hasChanged = JSON.stringify(newWorkers) !== JSON.stringify(oldWorkers)
          if (hasChanged) {
            set({ workers: newWorkers, lastRefreshed: Date.now() })

            // 如果当前选中的 Worker 已不存在，重置为全局视图
            if (currentWorker && !newWorkers.find((w) => w.id === currentWorker.id)) {
              set({ currentWorker: undefined })
              updateUrlWorkerParam('')
            }
          } else {
            // 即使数据没变，也更新时间戳
            set({ lastRefreshed: Date.now() })
          }
        } catch {
          // 静默刷新失败时不显示错误，避免打扰用户
        } finally {
          if (silentController === controller) {
            silentController = null
          }
        }
      },

      // 添加 Worker
      addWorker: (worker) => {
        set((state) => ({
          workers: [...state.workers, worker]
        }))
      },

      // 更新 Worker
      updateWorker: (workerId, updates) => {
        set((state) => ({
          workers: state.workers.map((w) =>
            w.id === workerId ? { ...w, ...updates } : w
          ),
          // 如果更新的是当前选中的 Worker，也更新 currentWorker
          currentWorker: state.currentWorker?.id === workerId
            ? { ...state.currentWorker, ...updates }
            : state.currentWorker
        }))
      },

      // 删除 Worker
      removeWorker: (workerId) => {
        set((state) => {
          const newWorkers = state.workers.filter((w) => w.id !== workerId)
          // 如果删除的是当前选中的 Worker，切换到全局视图
          const newCurrentWorker = state.currentWorker?.id === workerId
            ? undefined
            : state.currentWorker

          if (newCurrentWorker === undefined && state.currentWorker !== undefined) {
            updateUrlWorkerParam('')
          }

          return {
            workers: newWorkers,
            currentWorker: newCurrentWorker
          }
        })
      },

      // 清除错误
      clearError: () => {
        set({ error: '' })
      },

      // 从 URL 同步 Worker
      syncFromUrl: () => {
        const workerId = getWorkerIdFromUrl()
        if (workerId) {
          const { workers } = get()
          const worker = workers.find((w) => w.id === workerId)
          if (worker) {
            set({ currentWorker: worker })
          }
        }
      },

      // 同步到 URL
      syncToUrl: () => {
        const { currentWorker } = get()
        updateUrlWorkerParam(currentWorker?.id || '')
      }
    }),
    {
      name: 'worker-store',
      // 只持久化 currentWorker 的 id
      partialize: (state) => ({
        currentWorker: state.currentWorker,
        lastRefreshed: state.lastRefreshed
      })
    }
  )
)
