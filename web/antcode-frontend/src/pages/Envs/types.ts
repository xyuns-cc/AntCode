import type { RuntimePackage, RuntimeScope } from '@/services/runtimes'
import type { Worker } from '@/types'

/**
 * 扩展的运行时环境项，包含 Worker 信息
 */
export interface ExtendedRuntimeEnvItem {
  id: string
  scope: RuntimeScope
  key: string
  description?: string | null
  version: string
  runtime_locator: string
  created_by?: string | null
  created_by_username?: string | null
  created_at?: string | null
  updated_at?: string | null
  current_project_id?: string | null
  packages?: RuntimePackage[]
  workerName?: string
  workerId?: string
  envName?: string // Worker 环境名称（用于 runtime API 调用）
}

/**
 * 包信息
 */
export interface PackageInfo {
  name: string
  version: string
}

/**
 * 包列表模态框状态
 */
export interface PackageModalState {
  open: boolean
  env?: ExtendedRuntimeEnvItem
  packages?: PackageInfo[]
  loading?: boolean
}

/**
 * 编辑模态框状态
 */
export interface EditModalState {
  open: boolean
  env?: ExtendedRuntimeEnvItem
}

/**
 * 安装模态框状态
 */
export interface InstallModalState {
  open: boolean
  envId?: string
}

/**
 * 节点筛选选项
 */
export interface WorkerFilterOption {
  value: string
  label: string
}

/**
 * 环境列表页面 Props
 */
export type EnvListPageProps = Record<string, never>

/**
 * 编辑运行时环境标识模态框 Props
 */
export interface EditRuntimeEnvModalProps {
  open: boolean
  env?: ExtendedRuntimeEnvItem
  onClose: () => void
  onSuccess: () => void
}

/**
 * 安装依赖模态框 Props
 */
export interface InstallPackagesModalProps {
  open: boolean
  envId?: string
  onClose: () => void
  onSuccess: () => void
}

export type { RuntimeScope, Worker }
