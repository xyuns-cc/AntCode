export type WorkerDisplayStatus = 'running' | 'warning' | 'error' | 'stopped'
export type WorkerOs =
  'windows' | 'ubuntu' | 'debian' | 'centos' | 'redhat' | 'alpine' | 'fedora' | 'macos' | 'linux'

export interface WorkerDisplayData {
  id: string
  name: string
  version: string
  os: WorkerOs
  status: WorkerDisplayStatus
  cpu: number
  memory: number
  disk: number
  tasks: number
  uptime: string
  host: string
  port: number
  lastHeartbeat?: string
  cpuCores?: number
  memoryTotal?: number
  memoryUsed?: number
  memoryAvailable?: number
  diskTotal?: number
  diskUsed?: number
  diskFree?: number
}

export interface MonitorAlert {
  id: string
  type: 'error' | 'warning' | 'info'
  title: string
  message: string
  time: string
  worker: string
}

export interface MonitorTask {
  id: string
  name: string
  worker: string
  status: 'running' | 'success' | 'failed' | 'pending'
  cpu: number | string
  memory: number | string
}

export interface MonitorTaskCounts {
  success: number
  failed: number
  running: number
  pending: number
}

export interface ClusterHistory {
  timestamps: string[]
  cpu: MetricSeries
  memory: MetricSeries
}

export interface MetricSeries {
  avg: number[]
  max: number[]
  min: number[]
}

export interface WorkerHistoryPoint {
  timestamp: string
  cpu: number
  memory: number
  disk: number
}

export type PerformancePeriod = '24h' | '7d' | '30d'

export interface MonitorStats {
  totalWorkers: number
  onlineCount: number
  warningCount: number
  errorCount: number
  totalTasks: number
  systemStatus: 'normal' | 'warning' | 'error'
}
