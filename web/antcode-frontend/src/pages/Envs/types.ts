import type { VenvListItem, VenvScope } from '@/services/envs'
import type { Node } from '@/types'

/**
 * 扩展的环境项，包含节点信息
 */
export interface ExtendedVenvItem extends VenvListItem {
  nodeName?: string
  nodeId?: string
  isLocal: boolean
  envName?: string // 原始环境名称（用于节点环境API调用）
}

/**
 * 包信息
 */
export interface PackageInfo {
  name: string
  version: string
}

/**
 * 解释器信息
 */
export interface InterpreterInfo {
  version: string
  python_bin: string
  install_dir: string
  source?: string
  nodeName?: string
  nodeId?: string
}

/**
 * 包列表模态框状态
 */
export interface PackageModalState {
  open: boolean
  venv?: ExtendedVenvItem
  packages?: PackageInfo[]
  loading?: boolean
}

/**
 * 编辑模态框状态
 */
export interface EditModalState {
  open: boolean
  venv?: ExtendedVenvItem
}

/**
 * 安装模态框状态
 */
export interface InstallModalState {
  open: boolean
  venvId?: string
}

/**
 * 节点筛选选项
 */
export interface NodeFilterOption {
  value: string
  label: string
}

/**
 * 环境列表页面 Props
 */
export type EnvListPageProps = Record<string, never>

/**
 * 创建虚拟环境抽屉 Props
 */
export interface CreateVenvDrawerProps {
  onCreated: () => void
}

/**
 * 安装依赖按钮 Props
 */
export interface InstallPackagesButtonProps {
  venvId: string
  onInstalled?: () => void
  batch?: boolean
  selectedIds?: string[]
  buttonId?: string
}

/**
 * 编辑环境标识模态框 Props
 */
export interface EditVenvKeyModalProps {
  open: boolean
  venv?: ExtendedVenvItem
  onClose: () => void
  onSuccess: () => void
}

/**
 * 安装依赖模态框 Props
 */
export interface InstallPackagesModalProps {
  open: boolean
  venvId?: string
  onClose: () => void
  onSuccess: () => void
}

/**
 * 解释器抽屉 Props
 */
export interface InterpreterDrawerProps {
  onAdded: () => void
  currentNode?: Node | null
}

export type { VenvScope, Node }
