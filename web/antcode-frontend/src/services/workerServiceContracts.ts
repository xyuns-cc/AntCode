import type { Worker, WorkerResourceInfo, WorkerResourceUpdateRequest } from '@/types'

export type WorkerPermission = 'view' | 'use'

export interface WorkerConnectionTestResult {
  success: boolean
  // 后端 WorkerTestConnectionResponse 的 latency 有默认值 0 且随 response_model
  // 一定序列化出来，因此这里是必填。语义是**最后一次心跳距今的毫秒数**，
  // 不是往返延迟——后端只读 last_heartbeat，不向 Worker 发请求。
  latency: number
  error?: string
}

export interface WorkerInstallKey {
  key: string
  os_type: string
  allowed_source?: string
  install_command: string
  expires_at: string
}

export interface WorkerMetricHistoryPoint {
  timestamp: string
  cpu: number
  memory: number
  disk: number
  taskCount: number
  runningTasks: number
  uptime: number
}

interface MetricRange {
  avg: number[]
  max: number[]
  min: number[]
}

export interface ClusterMetricHistory {
  timestamps: string[]
  cpu: MetricRange
  memory: MetricRange
}

export interface WorkerUserPermission {
  user_id: string
  username: string
  permission: WorkerPermission
  assigned_at: string
  note?: string
}

export interface WorkerLoadRankingItem {
  worker_id: string
  name: string
  status: string
  load_score: number
  available: boolean
  // 后端字段曾叫 latency_ms，量的却是 now - last_heartbeat（心跳新鲜度），
  // 与网络往返无关；后端已改名，这里跟着改，别再按"延迟"去读它。
  heartbeat_age_ms: number
  metrics: {
    cpu: number
    memory: number
    runningTasks: number
    maxConcurrentTasks: number
  }
}

export interface BestWorkerResult {
  available: boolean
  worker?: Worker
  load_score?: number
  message?: string
}

// Worker 资源读写契约只在 types/worker.ts 声明一份。这里曾经有一份内联副本，
// 于是 limits 多出了一个后端从不返回的 task_timeout，界面上恒显示空值。
export type WorkerResourceDetails = WorkerResourceInfo
export type WorkerResourceUpdate = WorkerResourceUpdateRequest

export interface WorkerResourceUpdateResult {
  updated: Record<string, string>
  synced: boolean
}
