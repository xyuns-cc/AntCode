/**
 * 分布式节点类型定义
 */

// 节点状态
export type NodeStatus = 'online' | 'offline' | 'maintenance' | 'connecting'

// 节点指标
export interface NodeMetrics {
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
export interface RenderCapability {
  enabled: boolean
  browser_path?: string
  headless?: boolean
  max_instances?: number
  error?: string
}

// 节点能力
export interface NodeCapabilities {
  drissionpage?: RenderCapability
  curl_cffi?: {
    enabled: boolean
    default_impersonate?: string
  }
}

// 节点信息
export interface Node {
  id: string
  name: string
  host: string
  port: number
  status: NodeStatus
  region?: string
  tags?: string[]
  description?: string
  metrics?: NodeMetrics
  capabilities?: NodeCapabilities  // 节点能力
  version?: string
  // 操作系统信息
  osType?: string           // 操作系统类型: Windows/Linux/Darwin
  osVersion?: string        // 操作系统版本
  pythonVersion?: string    // Python 版本
  machineArch?: string      // CPU 架构: x86_64/arm64
  lastHeartbeat?: string
  createdAt: string
  updatedAt?: string
}

// 节点创建请求
export interface NodeCreateRequest {
  name: string
  host: string
  port: number
  region?: string
  tags?: string[]
  description?: string
}

// 节点更新请求
export interface NodeUpdateRequest {
  name?: string
  host?: string
  port?: number
  region?: string
  tags?: string[]
  description?: string
  status?: NodeStatus
}

// 节点列表参数
export interface NodeListParams {
  page?: number
  size?: number
  status?: NodeStatus
  region?: string
  search?: string
}

// 节点列表响应
export interface NodeListResponse {
  items: Node[]
  total: number
  page: number
  size: number
}

// 节点聚合统计
export interface NodeAggregateStats {
  totalNodes: number
  onlineNodes: number
  offlineNodes: number
  totalProjects: number
  totalTasks: number
  runningTasks: number
  totalEnvs: number
  avgCpu: number
  avgMemory: number
}

// 带节点信息的资源（用于全局视图）
export interface WithNodeInfo {
  node_id: string
  node_name: string
  node_status: NodeStatus
}

// 节点上下文
export interface NodeContextValue {
  currentNode: Node | null       // null 表示"全部节点"
  nodes: Node[]
  loading: boolean
  setCurrentNode: (node: Node | null) => void
  refreshNodes: () => Promise<void>
  isGlobalView: boolean          // 是否为全局视图
}

// 本地存储的节点偏好
export interface NodePreference {
  lastNodeId: string | null
  viewMode: 'global' | 'single'
}

// 节点资源限制
export interface NodeResourceLimits {
  max_concurrent_tasks: number      // 最大并发任务数
  task_memory_limit_mb: number      // 单任务内存限制 (MB)
  task_cpu_time_limit_sec: number   // 单任务CPU时间限制 (秒)
  task_timeout: number              // 任务超时时间 (秒)
}

// 节点资源统计
export interface NodeResourceStats {
  cpu_percent: number               // 当前CPU使用率
  memory_percent: number            // 当前内存使用率
  memory_available_gb: number       // 可用内存 (GB)
  memory_total_gb: number           // 总内存 (GB)
  cpu_history_avg: number           // CPU历史平均
  memory_history_avg: number        // 内存历史平均
  current_limits: {
    max_concurrent_tasks: number
    task_memory_limit_mb: number
    task_cpu_time_limit_sec: number
  }
  auto_adjustment_enabled: boolean  // 是否启用自适应调整
  monitoring_active: boolean        // 监控是否活跃
}

// 节点资源信息
export interface NodeResourceInfo {
  limits: NodeResourceLimits
  auto_adjustment: boolean
  resource_stats: NodeResourceStats
}

// 节点资源更新请求
export interface NodeResourceUpdateRequest {
  max_concurrent_tasks?: number     // 1-20
  task_memory_limit_mb?: number     // 256-8192
  task_cpu_time_limit_sec?: number  // 60-3600
  auto_resource_limit?: boolean
}

