/**
 * 系统配置相关类型定义
 */

/**
 * 系统配置基础接口
 */
export interface SystemConfig {
  config_key: string
  config_value: string
  category: string
  description?: string
  value_type: 'string' | 'int' | 'float' | 'bool' | 'json'
  is_active: boolean
  modified_by?: string
  created_at: string
  updated_at: string
}

/**
 * 创建系统配置请求
 */
export interface CreateSystemConfigRequest {
  config_key: string
  config_value: string
  category: string
  description?: string
  value_type?: 'string' | 'int' | 'float' | 'bool' | 'json'
  is_active?: boolean
}

/**
 * 更新系统配置请求
 */
export interface UpdateSystemConfigRequest {
  config_value?: string
  description?: string
  is_active?: boolean
}

/**
 * 批量更新配置请求
 */
export interface BatchUpdateConfigRequest {
  configs: Array<{
    config_key: string
    config_value: string
    is_active?: boolean
    category?: string
    description?: string
    value_type?: string
  }>
}

/**
 * 任务资源配置
 */
export interface TaskResourceConfig {
  max_concurrent_tasks: number  // 最大并发任务数
  task_execution_timeout: number  // 任务执行超时时间（秒）
  task_cpu_time_limit: number  // 任务CPU时间限制（秒）
  task_memory_limit: number  // 任务内存限制（MB）
  task_max_retries: number  // 任务最大重试次数
  task_retry_delay: number  // 任务重试延迟（秒）
}

/**
 * 任务日志配置
 */
export interface TaskLogConfig {
  task_log_retention_days: number  // 日志保留天数
  task_log_max_size: number  // 日志最大大小（字节）
}

/**
 * 调度器配置
 */
export interface SchedulerConfig {
  scheduler_timezone: string  // 调度器时区
  cleanup_workspace_on_completion: boolean  // 完成后清理工作空间
  cleanup_workspace_max_age_hours: number  // 工作空间最大保留时间（小时）
}

/**
 * 缓存配置
 */
export interface CacheConfig {
  cache_enabled: boolean  // 是否启用缓存
  cache_default_ttl: number  // 默认缓存TTL（秒）
  metrics_cache_ttl: number  // 指标缓存TTL（秒）
  api_cache_ttl: number  // API缓存TTL（秒）
  users_cache_ttl: number  // 用户缓存TTL（秒）
  query_cache_ttl: number  // 查询缓存TTL（秒）
  metrics_background_update: boolean  // 是否启用指标后台更新
  metrics_update_interval: number  // 指标更新间隔（秒）
}

/**
 * 监控配置
 */
export interface MonitoringConfig {
  monitoring_enabled: boolean  // 是否启用监控
  monitor_status_ttl: number  // 监控状态TTL（秒）
  monitor_history_ttl: number  // 监控历史TTL（秒）
  monitor_history_keep_days: number  // 监控历史保留天数
  monitor_cluster_ttl: number  // 集群状态TTL（秒）
  monitor_stream_batch_size: number  // 监控流批处理大小
  monitor_stream_interval: number  // 监控流处理间隔（秒）
  monitor_stream_maxlen: number  // 监控流最大长度
}

/**
 * 所有系统配置
 */
export interface AllSystemConfigs {
  task_resource: TaskResourceConfig
  task_log: TaskLogConfig
  scheduler: SchedulerConfig
  cache: CacheConfig
  monitoring: MonitoringConfig
}

/**
 * 配置分类
 */
export const CONFIG_CATEGORIES = {
  TASK_RESOURCE: 'task_resource',
  TASK_LOG: 'task_log',
  SCHEDULER: 'scheduler',
  CACHE: 'cache',
  MONITORING: 'monitoring',
} as const

/**
 * 配置分类标签
 */
export const CONFIG_CATEGORY_LABELS: Record<string, string> = {
  task_resource: '任务资源配置',
  task_log: '任务日志配置',
  scheduler: '调度器配置',
  cache: '缓存配置',
  monitoring: '监控配置',
}

/**
 * 配置字段标签映射
 */
export const CONFIG_FIELD_LABELS: Record<string, string> = {
  // 任务资源配置
  max_concurrent_tasks: '最大并发任务数',
  task_execution_timeout: '任务执行超时时间（秒）',
  task_cpu_time_limit: '任务CPU时间限制（秒）',
  task_memory_limit: '任务内存限制（MB）',
  task_max_retries: '任务最大重试次数',
  task_retry_delay: '任务重试延迟（秒）',
  
  // 任务日志配置
  task_log_retention_days: '日志保留天数',
  task_log_max_size: '日志最大大小',
  
  // 调度器配置
  scheduler_timezone: '调度器时区',
  cleanup_workspace_on_completion: '完成后清理工作空间',
  cleanup_workspace_max_age_hours: '工作空间最大保留时间（小时）',
  
  // 缓存配置
  cache_enabled: '启用缓存',
  cache_default_ttl: '默认缓存TTL（秒）',
  metrics_cache_ttl: '指标缓存TTL（秒）',
  api_cache_ttl: 'API缓存TTL（秒）',
  users_cache_ttl: '用户缓存TTL（秒）',
  query_cache_ttl: '查询缓存TTL（秒）',
  metrics_background_update: '启用指标后台更新',
  metrics_update_interval: '指标更新间隔（秒）',
  
  // 监控配置
  monitoring_enabled: '启用监控',
  monitor_status_ttl: '监控状态TTL（秒）',
  monitor_history_ttl: '监控历史TTL（秒）',
  monitor_history_keep_days: '监控历史保留天数',
  monitor_cluster_ttl: '集群状态TTL（秒）',
  monitor_stream_batch_size: '监控流批处理大小',
  monitor_stream_interval: '监控流处理间隔（秒）',
  monitor_stream_maxlen: '监控流最大长度',
}

/**
 * 配置字段描述映射
 */
export const CONFIG_FIELD_DESCRIPTIONS: Record<string, string> = {
  max_concurrent_tasks: '系统同时执行的最大任务数量，范围: 1-100',
  task_execution_timeout: '单个任务允许的最长执行时间，范围: 60-86400秒',
  task_cpu_time_limit: '单个任务的CPU时间限制，范围: 60-3600秒',
  task_memory_limit: '单个任务的内存使用限制，范围: 128-8192MB',
  task_max_retries: '任务失败后的最大重试次数，范围: 0-10',
  task_retry_delay: '任务重试前的等待时间，范围: 10-600秒',
  
  task_log_retention_days: '日志文件的保留天数，范围: 1-365天',
  task_log_max_size: '单个日志文件的最大大小，范围: 1-1024 MB',
  
  scheduler_timezone: '任务调度器使用的时区，如: Asia/Shanghai',
  cleanup_workspace_on_completion: '任务完成后是否自动清理工作空间',
  cleanup_workspace_max_age_hours: '工作空间文件的最大保留时间，范围: 1-168小时',
  
  cache_enabled: '是否启用系统缓存功能',
  cache_default_ttl: '默认缓存过期时间，范围: 60-3600秒',
  metrics_cache_ttl: '系统指标缓存过期时间，范围: 10-300秒',
  api_cache_ttl: 'API响应缓存过期时间，范围: 60-3600秒',
  users_cache_ttl: '用户信息缓存过期时间，范围: 60-3600秒',
  query_cache_ttl: '查询结果缓存过期时间，范围: 60-3600秒',
  metrics_background_update: '是否启用指标后台自动更新',
  metrics_update_interval: '指标后台更新间隔，范围: 5-300秒',
  
  monitoring_enabled: '是否启用系统监控功能',
  monitor_status_ttl: '监控状态信息的缓存时间，范围: 60-3600秒',
  monitor_history_ttl: '监控历史数据的缓存时间，范围: 600-86400秒',
  monitor_history_keep_days: '监控历史数据的保留天数，范围: 1-365天',
  monitor_cluster_ttl: '集群状态信息的缓存时间，范围: 60-3600秒',
  monitor_stream_batch_size: '监控数据流每批处理的数量，范围: 10-1000',
  monitor_stream_interval: '监控数据流处理间隔，范围: 30-600秒',
  monitor_stream_maxlen: '监控数据流最大长度，范围: 1000-100000',
}

