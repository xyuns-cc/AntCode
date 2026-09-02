export type WorkerDisplayStatus = 'running' | 'warning' | 'error' | 'stopped'
export type WorkerOs =
  'windows' | 'ubuntu' | 'debian' | 'centos' | 'redhat' | 'alpine' | 'fedora' | 'macos' | 'linux'

export interface WorkerDisplayData {
  id: string
  name: string
  version: string
  os: WorkerOs
  status: WorkerDisplayStatus
  // 这台机器上报过指标吗。cpu/memory/disk 缺指标时取 0（阈值判定与条形图都要数字），
  // 光看它们分不出「真的 0%」和「没上报过」，集群均值必须靠这个标志把后者排除掉。
  hasMetrics: boolean
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
