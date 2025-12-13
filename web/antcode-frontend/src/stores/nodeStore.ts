/**
 * 节点状态管理
 * 管理当前选中的节点和节点列表
 */
import { create } from 'zustand'
import { persist } from 'zustand/middleware'
import type { Node } from '@/types'
import { nodeService } from '@/services/nodes'

interface NodeState {
  // 当前选中的节点，null 表示"全部节点"
  currentNode: Node | null
  // 所有节点列表
  nodes: Node[]
  // 加载状态
  loading: boolean
  // 错误信息
  error: string | null
  // 最后刷新时间
  lastRefreshed: number | null
}

interface NodeActions {
  // 设置当前节点
  setCurrentNode: (node: Node | null) => void
  // 设置节点列表
  setNodes: (nodes: Node[]) => void
  // 刷新节点列表（显示loading）
  refreshNodes: () => Promise<void>
  // 静默刷新节点列表（不显示loading，用于后台自动更新）
  silentRefresh: () => Promise<void>
  // 添加节点
  addNode: (node: Node) => void
  // 更新节点
  updateNode: (nodeId: string, updates: Partial<Node>) => void
  // 删除节点
  removeNode: (nodeId: string) => void
  // 清除错误
  clearError: () => void
  // 从 URL 同步节点
  syncFromUrl: () => void
  // 同步到 URL
  syncToUrl: () => void
}

type NodeStore = NodeState & NodeActions

// 从 URL 获取节点 ID
const getNodeIdFromUrl = (): string | null => {
  if (typeof window === 'undefined') return null
  const params = new URLSearchParams(window.location.search)
  return params.get('node')
}

// 更新 URL 中的节点参数
const updateUrlNodeParam = (nodeId: string | null) => {
  if (typeof window === 'undefined') return
  const url = new URL(window.location.href)
  if (nodeId) {
    url.searchParams.set('node', nodeId)
  } else {
    url.searchParams.delete('node')
  }
  window.history.replaceState({}, '', url.toString())
}

export const useNodeStore = create<NodeStore>()(
  persist(
    (set, get) => ({
      // 初始状态
      currentNode: null,
      nodes: [],
      loading: false,
      error: null,
      lastRefreshed: null,

      // 设置当前节点
      setCurrentNode: (node) => {
        set({ currentNode: node })
        updateUrlNodeParam(node?.id || null)
      },

      // 设置节点列表
      setNodes: (nodes) => {
        set({ nodes, lastRefreshed: Date.now() })
      },

      // 刷新节点列表（显示loading状态）
      refreshNodes: async () => {
        set({ loading: true, error: null })
        try {
          const nodes = await nodeService.getAllNodes()
          set({ nodes, loading: false, lastRefreshed: Date.now() })
          
          // 如果当前选中的节点已不存在，重置为全局视图
          const { currentNode } = get()
          if (currentNode && !nodes.find(n => n.id === currentNode.id)) {
            set({ currentNode: null })
            updateUrlNodeParam(null)
          }
        } catch (error: unknown) {
          const message = error instanceof Error ? error.message : '获取节点列表失败'
          set({
            loading: false,
            error: message
          })
        }
      },

      // 静默刷新节点列表（不显示loading，用于后台自动更新）
      silentRefresh: async () => {
        try {
          const newNodes = await nodeService.getAllNodes()
          const { nodes: oldNodes, currentNode } = get()
          
          // 只有当数据真正变化时才更新（避免不必要的重渲染）
          const hasChanged = JSON.stringify(newNodes) !== JSON.stringify(oldNodes)
          if (hasChanged) {
            set({ nodes: newNodes, lastRefreshed: Date.now() })
            
            // 如果当前选中的节点已不存在，重置为全局视图
            if (currentNode && !newNodes.find(n => n.id === currentNode.id)) {
              set({ currentNode: null })
              updateUrlNodeParam(null)
            }
          } else {
            // 即使数据没变，也更新时间戳
            set({ lastRefreshed: Date.now() })
          }
        } catch {
          // 静默刷新失败时不显示错误，避免打扰用户
        }
      },

      // 添加节点
      addNode: (node) => {
        set(state => ({
          nodes: [...state.nodes, node]
        }))
      },

      // 更新节点
      updateNode: (nodeId, updates) => {
        set(state => ({
          nodes: state.nodes.map(n => 
            n.id === nodeId ? { ...n, ...updates } : n
          ),
          // 如果更新的是当前选中的节点，也更新 currentNode
          currentNode: state.currentNode?.id === nodeId 
            ? { ...state.currentNode, ...updates }
            : state.currentNode
        }))
      },

      // 删除节点
      removeNode: (nodeId) => {
        set(state => {
          const newNodes = state.nodes.filter(n => n.id !== nodeId)
          // 如果删除的是当前选中的节点，切换到全局视图
          const newCurrentNode = state.currentNode?.id === nodeId 
            ? null 
            : state.currentNode
          
          if (newCurrentNode === null && state.currentNode !== null) {
            updateUrlNodeParam(null)
          }
          
          return { 
            nodes: newNodes, 
            currentNode: newCurrentNode 
          }
        })
      },

      // 清除错误
      clearError: () => {
        set({ error: null })
      },

      // 从 URL 同步节点
      syncFromUrl: () => {
        const nodeId = getNodeIdFromUrl()
        if (nodeId) {
          const { nodes } = get()
          const node = nodes.find(n => n.id === nodeId)
          if (node) {
            set({ currentNode: node })
          }
        }
      },

      // 同步到 URL
      syncToUrl: () => {
        const { currentNode } = get()
        updateUrlNodeParam(currentNode?.id || null)
      }
    }),
    {
      name: 'node-store',
      // 只持久化 currentNode 的 id
      partialize: (state) => ({
        currentNodeId: state.currentNode?.id || null
      }),
      // 恢复时通过 id 查找节点
      onRehydrateStorage: () => (state) => {
        if (state) {
          // 延迟执行，等待节点列表加载
          setTimeout(() => {
            state.syncFromUrl()
          }, 100)
        }
      }
    }
  )
)

// 选择器 hooks
export const useCurrentNode = () => useNodeStore(state => state.currentNode)
export const useNodes = () => useNodeStore(state => state.nodes)
export const useNodeLoading = () => useNodeStore(state => state.loading)
export const useIsGlobalView = () => useNodeStore(state => state.currentNode === null)

// 获取节点参数（用于 API 调用）
export const getNodeParam = (): string | undefined => {
  const { currentNode } = useNodeStore.getState()
  return currentNode?.id || undefined
}

export default useNodeStore
