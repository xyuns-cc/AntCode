import { create } from 'zustand'
import { immer } from 'zustand/middleware/immer'
import type { Project, ProjectListParams, ProjectStats } from '@/types'

interface ProjectStore {
  // 状态
  projects: Project[]
  currentProject: Project | null
  stats: ProjectStats | null
  isLoading: boolean
  error: string | null
  
  // 分页和过滤
  pagination: {
    current: number
    pageSize: number
    total: number
  }
  filters: ProjectListParams
  
  // 操作方法
  setProjects: (projects: Project[]) => void
  addProject: (project: Project) => void
  updateProject: (id: string, updates: Partial<Project>) => void
  removeProject: (id: string) => void
  setCurrentProject: (project: Project | null) => void
  setStats: (stats: ProjectStats) => void
  setLoading: (loading: boolean) => void
  setError: (error: string | null) => void
  setPagination: (pagination: Partial<ProjectStore['pagination']>) => void
  setFilters: (filters: Partial<ProjectListParams>) => void
  resetFilters: () => void
  
  // 查询方法
  getProjectById: (id: string) => Project | undefined
  getProjectsByType: (type: string) => Project[]
  getProjectsByStatus: (status: string) => Project[]
  searchProjects: (query: string) => Project[]
}

const initialFilters: ProjectListParams = {
  page: 1,
  size: 10,
  type: undefined,
  status: undefined,
  tag: undefined,
  search: undefined,
  sort_by: 'created_at',
  sort_order: 'desc',
  created_by: undefined
}

export const useProjectStore = create<ProjectStore>()(
  immer((set, get) => ({
    // 初始状态
    projects: [],
    currentProject: null,
    stats: null,
    isLoading: false,
    error: null,
    pagination: {
      current: 1,
      pageSize: 10,
      total: 0
    },
    filters: initialFilters,

    // 设置项目列表
    setProjects: (projects: Project[]) => {
      set((state) => {
        state.projects = projects
        state.error = null
      })
    },

    // 添加项目
    addProject: (project: Project) => {
      set((state) => {
        state.projects.unshift(project)
        state.pagination.total += 1
      })
    },

    // 更新项目
    updateProject: (id: string, updates: Partial<Project>) => {
      set((state) => {
        const index = state.projects.findIndex(p => p.id === id)
        if (index !== -1) {
          state.projects[index] = { ...state.projects[index], ...updates }
        }
        
        // 如果是当前项目，也要更新
        if (state.currentProject?.id === id) {
          state.currentProject = { ...state.currentProject, ...updates }
        }
      })
    },

    // 删除项目
    removeProject: (id: string) => {
      set((state) => {
        state.projects = state.projects.filter(p => p.id !== id)
        state.pagination.total -= 1
        
        // 如果删除的是当前项目，清空当前项目
        if (state.currentProject?.id === id) {
          state.currentProject = null
        }
      })
    },

    // 设置当前项目
    setCurrentProject: (project: Project | null) => {
      set((state) => {
        state.currentProject = project
      })
    },

    // 设置统计信息
    setStats: (stats: ProjectStats) => {
      set((state) => {
        state.stats = stats
      })
    },

    // 设置加载状态
    setLoading: (loading: boolean) => {
      set((state) => {
        state.isLoading = loading
      })
    },

    // 设置错误信息
    setError: (error: string | null) => {
      set((state) => {
        state.error = error
      })
    },

    // 设置分页信息
    setPagination: (pagination: Partial<ProjectStore['pagination']>) => {
      set((state) => {
        state.pagination = { ...state.pagination, ...pagination }
      })
    },

    // 设置过滤条件
    setFilters: (filters: Partial<ProjectListParams>) => {
      set((state) => {
        state.filters = { ...state.filters, ...filters }
        // 重置分页到第一页
        if (Object.keys(filters).some(key => key !== 'page')) {
          state.pagination.current = 1
        }
      })
    },

    // 重置过滤条件
    resetFilters: () => {
      set((state) => {
        state.filters = initialFilters
        state.pagination.current = 1
      })
    },

    // 根据ID获取项目
    getProjectById: (id: string) => {
      return get().projects.find(p => p.id === id)
    },

    // 根据类型获取项目
    getProjectsByType: (type: string) => {
      return get().projects.filter(p => p.type === type)
    },

    // 根据状态获取项目
    getProjectsByStatus: (status: string) => {
      return get().projects.filter(p => p.status === status)
    },

    // 搜索项目
    searchProjects: (query: string) => {
      const { projects } = get()
      const lowerQuery = query.toLowerCase()
      return projects.filter(p => 
        p.name.toLowerCase().includes(lowerQuery) ||
        p.description?.toLowerCase().includes(lowerQuery) ||
        p.tags?.toLowerCase().includes(lowerQuery)
      )
    }
  }))
)

// 选择器函数
export const selectProjects = (state: ProjectStore) => state.projects
export const selectCurrentProject = (state: ProjectStore) => state.currentProject
export const selectProjectStats = (state: ProjectStore) => state.stats
export const selectProjectLoading = (state: ProjectStore) => state.isLoading
export const selectProjectError = (state: ProjectStore) => state.error
export const selectProjectPagination = (state: ProjectStore) => state.pagination
export const selectProjectFilters = (state: ProjectStore) => state.filters

// Hook 封装
export const useProjects = () => {
  const store = useProjectStore()
  return {
    projects: store.projects,
    currentProject: store.currentProject,
    stats: store.stats,
    isLoading: store.isLoading,
    error: store.error,
    pagination: store.pagination,
    filters: store.filters,
    setProjects: store.setProjects,
    addProject: store.addProject,
    updateProject: store.updateProject,
    removeProject: store.removeProject,
    setCurrentProject: store.setCurrentProject,
    setStats: store.setStats,
    setLoading: store.setLoading,
    setError: store.setError,
    setPagination: store.setPagination,
    setFilters: store.setFilters,
    resetFilters: store.resetFilters,
    getProjectById: store.getProjectById,
    getProjectsByType: store.getProjectsByType,
    getProjectsByStatus: store.getProjectsByStatus,
    searchProjects: store.searchProjects
  }
}

export default useProjectStore
