/**
 * 项目列表页的 UI 状态机
 * 抽屉/弹窗开关与选中项集中在此，页面组件只负责编排
 */
import type React from 'react'
import type { Project } from '@/types'

export interface UIState {
  loading: boolean
  createDrawerVisible: boolean
  editDrawerVisible: boolean
  selectedRowKeys: React.Key[]
  selectedProjects: Project[]
  deleteModalVisible: boolean
  batchDeleteModalVisible: boolean
  currentDeleteProject: Project | null
  currentEditProject: Project | null
}

export type UIAction =
  | { type: 'SET_LOADING'; payload: boolean }
  | { type: 'TOGGLE_CREATE_DRAWER'; payload?: boolean }
  | { type: 'TOGGLE_EDIT_DRAWER'; payload?: boolean }
  | { type: 'SET_CURRENT_EDIT_PROJECT'; payload: Project | null }
  | { type: 'SET_SELECTED_PROJECTS'; payload: { keys: React.Key[]; projects: Project[] } }
  | { type: 'SHOW_DELETE_MODAL'; payload: Project }
  | { type: 'HIDE_DELETE_MODAL' }
  | { type: 'SHOW_BATCH_DELETE_MODAL' }
  | { type: 'HIDE_BATCH_DELETE_MODAL' }
  | { type: 'CLEAR_SELECTION' }
  | { type: 'REMOVE_SELECTED_PROJECT'; payload: string }

export const initialUIState: UIState = {
  loading: false,
  createDrawerVisible: false,
  editDrawerVisible: false,
  selectedRowKeys: [],
  selectedProjects: [],
  deleteModalVisible: false,
  batchDeleteModalVisible: false,
  currentDeleteProject: null,
  currentEditProject: null,
}

export function uiReducer(state: UIState, action: UIAction): UIState {
  switch (action.type) {
    case 'SET_LOADING':
      return { ...state, loading: action.payload }
    case 'TOGGLE_CREATE_DRAWER':
      return {
        ...state,
        createDrawerVisible: action.payload ?? !state.createDrawerVisible,
      }
    case 'TOGGLE_EDIT_DRAWER':
      return {
        ...state,
        editDrawerVisible: action.payload ?? !state.editDrawerVisible,
      }
    case 'SET_CURRENT_EDIT_PROJECT':
      return {
        ...state,
        currentEditProject: action.payload,
      }
    case 'SET_SELECTED_PROJECTS':
      return {
        ...state,
        selectedRowKeys: action.payload.keys,
        selectedProjects: action.payload.projects,
      }
    case 'SHOW_DELETE_MODAL':
      return {
        ...state,
        deleteModalVisible: true,
        currentDeleteProject: action.payload,
      }
    case 'HIDE_DELETE_MODAL':
      return {
        ...state,
        deleteModalVisible: false,
        currentDeleteProject: null,
      }
    case 'SHOW_BATCH_DELETE_MODAL':
      return {
        ...state,
        batchDeleteModalVisible: true,
      }
    case 'HIDE_BATCH_DELETE_MODAL':
      return {
        ...state,
        batchDeleteModalVisible: false,
      }
    case 'CLEAR_SELECTION':
      return {
        ...state,
        selectedRowKeys: [],
        selectedProjects: [],
      }
    case 'REMOVE_SELECTED_PROJECT':
      return {
        ...state,
        selectedRowKeys: state.selectedRowKeys.filter((key) => key !== action.payload),
        selectedProjects: state.selectedProjects.filter((p) => p.id !== action.payload),
      }
    default:
      return state
  }
}
