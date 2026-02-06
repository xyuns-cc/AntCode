/**
 * 编辑器状态管理 Store
 * 管理文件编辑的 dirty 状态、版本信息等
 */
import { create } from 'zustand'
import type { EditStatus, ProjectVersion } from '@/types/version'

interface EditorState {
  // 当前项目 ID
  projectId: string | null
  
  // 编辑状态（来自服务端）
  editStatus: EditStatus | null
  
  // 本地 dirty 状态（双保险）
  localDirty: boolean
  
  // 当前文件的 ETag
  currentEtag: string | null
  
  // 版本列表
  versions: ProjectVersion[]
  
  // 当前查看的版本（draft/latest/数字）
  currentVersion: string
  
  // 加载状态
  loading: boolean
  
  // Actions
  setProjectId: (id: string | null) => void
  setEditStatus: (status: EditStatus | null) => void
  setLocalDirty: (dirty: boolean) => void
  setCurrentEtag: (etag: string | null) => void
  setVersions: (versions: ProjectVersion[]) => void
  setCurrentVersion: (version: string) => void
  setLoading: (loading: boolean) => void
  
  // 重置状态
  reset: () => void
  
  // 计算属性
  isDirty: () => boolean
  hasPublishedVersions: () => boolean
}

const initialState = {
  projectId: null,
  editStatus: null,
  localDirty: false,
  currentEtag: null,
  versions: [],
  currentVersion: 'draft',
  loading: false,
}

export const useEditorStore = create<EditorState>((set, get) => ({
  ...initialState,
  
  setProjectId: (id) => set({ projectId: id }),
  
  setEditStatus: (status) => set({ editStatus: status }),
  
  setLocalDirty: (dirty) => set({ localDirty: dirty }),
  
  setCurrentEtag: (etag) => set({ currentEtag: etag }),
  
  setVersions: (versions) => set({ versions }),
  
  setCurrentVersion: (version) => set({ currentVersion: version }),
  
  setLoading: (loading) => set({ loading }),
  
  reset: () => set(initialState),
  
  // 综合判断是否有未保存修改
  isDirty: () => {
    const state = get()
    return state.localDirty || (state.editStatus?.dirty ?? false)
  },
  
  // 是否有已发布版本
  hasPublishedVersions: () => {
    const state = get()
    return state.versions.length > 0 || (state.editStatus?.published_version ?? 0) > 0
  },
}))

export default useEditorStore
