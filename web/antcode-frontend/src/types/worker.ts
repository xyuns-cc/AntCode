/**
 * 分布式 Worker 类型定义
 */

// Worker 状态
export type WorkerStatus = 'online' | 'offline' | 'maintenance' | 'connecting'

// Worker 指标
export interface WorkerMetrics {
  cpu: number                 // CPU 使用率 (%)
  memory: number              // 内存使用率 (%)
  disk: number                // 磁盘使用率 (%)
  taskCount: number
  runningTasks: number
  projectCount: number
  envCount: number
  uptime: number              // 运行时间（秒）
  // 详细资源信息
  cpuCores?: number           // CPU 核心数
  memoryTotal?: number        // 总内存 (bytes)
  memoryUsed?: number         // 已用内存 (bytes)
  memoryAvailable?: number    // 可用内存 (bytes)
  diskTotal?: number          // 总磁盘 (bytes)
  diskUsed?: number           // 已用磁盘 (bytes)
  diskFree?: number           // 可用磁盘 (bytes)
}

// 渲染能力详情
export interface WorkerRenderCapability {
  enabled: boolean
  browser_path?: string
  headless?: boolean
  max_instances?: number
  error?: string
}

// Worker 能力
export interface WorkerCapabilities {
  drissionpage?: WorkerRenderCapability
  curl_cffi?: {
    enabled: boolean
    default_impersonate?: string
  }
}

// Worker 信息
export interface Worker {
  id: string
  name: string
  host: string
  port: number
  status: WorkerStatus
  region?: string
  tags?: string[]
  description?: string
  metrics?: WorkerMetrics
  capabilities?: WorkerCapabilities  // Worker 能力
  hasRenderCapability?: boolean
  version?: string
  // 操作系统信息
  osType?: string           // 操作系统类型: Windows/Linux/Darwin
  osVersion?: string        // 操作系统版本
  pythonVersion?: string    // Python 版本
  machineArch?: string      // CPU 架构: x86_64/arm64
  // 连接模式
  transportMode?: 'direct' | 'gateway'  // 连接模式: direct/gateway
  lastHeartbeat?: string
  createdAt: string
  updatedAt?: string
}

// Worker 创建请求
export interface WorkerCreateRequest {
  name: string
  host: string
  port: number
  region?: string
  tags?: string[]
  description?: string
}

// Worker 更新请求
export interface WorkerUpdateRequest {
  name?: string
  host?: string
  port?: number
  region?: string
  tags?: string[]
  description?: string
  status?: WorkerStatus
}

// Worker 列表参数
export interface WorkerListParams {
  page?: number
  size?: number
  status?: WorkerStatus
  region?: string
  search?: string
}

// Worker 列表响应
export interface WorkerListResponse {
  items: Worker[]
  total: number
  page: number
  size: number
}

// Worker 聚合统计
export interface WorkerAggregateStats {
  totalWorkers: number
  onlineWorkers: number
  offlineWorkers: number
  maintenanceWorkers: number
  totalProjects: number
  totalTasks: number
  runningTasks: number
  totalEnvs: number
  avgCpu: number
  avgMemory: number
  totalRequests: number
  totalResponses: number
  totalItemsScraped: number
  totalErrors: number
  avgLatencyMs: number
  clusterRequestsPerMinute: number
}

// 带 Worker 信息的资源（用于全局视图）
export interface WithWorkerInfo {
  worker_id: string
  worker_name: string
  worker_status: WorkerStatus
}

// Worker 上下文
export interface WorkerContextValue {
  currentWorker: Worker | undefined       // undefined 表示"全部 Worker"
  workers: Worker[]
  loading: boolean
  setCurrentWorker: (worker: Worker | undefined) => void
  refreshWorkers: () => Promise<void>
  isGlobalView: boolean          // 是否为全局视图
}

// 本地存储的 Worker 偏好
export interface WorkerPreference {
  lastWorkerId: string
  viewMode: 'global' | 'single'
}

// Worker 资源限制
export interface WorkerResourceLimits {
  max_concurrent_tasks: number      // 最大并发任务数
  task_memory_limit_mb: number      // 单任务内存限制 (MB)
  task_cpu_time_limit_sec: number   // 单任务CPU时间限制 (秒)
  task_timeout?: number             // 任务超时时间 (秒)
}

// Worker 资源统计
export interface WorkerResourceStats {
  cpu_percent: number
  memory_percent: number
  disk_percent: number
  memory_used_mb: number
  memory_total_mb: number
  disk_used_gb: number
  disk_total_gb: number
  running_tasks: number
  queued_tasks: number
  uptime_seconds: number
}

// Worker 资源信息
export interface WorkerResourceInfo {
  limits: WorkerResourceLimits
  auto_adjustment: boolean
  resource_stats: WorkerResourceStats
}

// Worker 资源更新请求
export interface WorkerResourceUpdateRequest {
  max_concurrent_tasks?: number     // 1-20
  task_memory_limit_mb?: number     // 256-8192
  task_cpu_time_limit_sec?: number  // 60-3600
  auto_resource_limit?: boolean
}

// 爬虫统计摘要
export interface SpiderStatsSummary {
  requestCount: number              // 请求总数
  responseCount: number             // 响应总数
  itemScrapedCount: number          // 抓取数据项数
  errorCount: number                // 错误总数
  avgLatencyMs: number              // 平均延迟(毫秒)
  requestsPerMinute: number         // 每分钟请求数
  statusCodes: Record<string, number> // 状态码分布
}

// 域名统计
export interface DomainStats {
  domain: string
  reqs: number
  successRate: number
  latency: number
  status: 'Healthy' | 'Warning' | 'Critical'
}

// 集群爬虫统计
export interface ClusterSpiderStats {
  totalRequests: number
  totalResponses: number
  totalItemsScraped: number
  totalErrors: number
  avgLatencyMs: number
  domainStats?: DomainStats[]
  clusterRequestsPerMinute: number
  workerCount: number
  statusCodes: Record<string, number>
}

// 爬虫统计历史点
export interface SpiderStatsHistoryPoint {
  timestamp: string
  requestCount: number
  responseCount: number
  itemScrapedCount: number
  errorCount: number
  avgLatencyMs: number
  requestsPerMinute: number
}

// Worker 安装 Key 响应
export interface WorkerInstallKeyResponse {
  key: string
  os_type: string
  install_command: string
  expires_at: string
}
