export type ProjectType = 'file' | 'rule' | 'code'
export type ProjectStatus = 'active' | 'inactive' | 'archived'
export type CrawlEngine = 'requests' | 'curl_cffi' | 'playwright'
export type ExecutionStrategy = 'fixed' | 'specified' | 'auto' | 'prefer'

export interface RuleDedupConfig {
  enabled?: boolean
  fields?: string[]
  scope?: 'project' | 'run'
  ttl_days?: number
  on_hit?: 'drop' | 'log'
}

export interface ExecutionStrategyConfig {
  strategy: ExecutionStrategy
  bound_worker_id?: string
  bound_worker_name?: string
}

export const EXECUTION_STRATEGY_OPTIONS = [
  { value: 'fixed', label: '固定 Worker', description: '仅在绑定 Worker 执行，不可用时失败' },
  { value: 'specified', label: '指定 Worker', description: '在指定的 Worker 上执行' },
  { value: 'auto', label: '自动选择', description: '根据负载自动选择最优 Worker' },
  {
    value: 'prefer',
    label: '优先绑定 Worker',
    description: '优先使用绑定 Worker，不可用时自动选择其他 Worker',
  },
] as const

export interface RuntimeConfig {
  timeout?: number
  memory_limit?: number
  cpu_limit?: number
  max_retries?: number
  [key: string]: unknown
}

export interface EnvironmentVars {
  [key: string]: string
}

export interface DataSchema {
  [field: string]: {
    type: 'string' | 'number' | 'boolean' | 'array' | 'object'
    required?: boolean
    description?: string
  }
}

export interface HttpHeaders {
  [key: string]: string
}

export interface HttpCookies {
  [key: string]: string
}

export interface Project {
  id: string
  name: string
  type: ProjectType
  status: ProjectStatus
  description?: string
  tags?: string[]
  created_at: string
  updated_at: string
  created_by: string
  created_by_username?: string
  env_location?: 'worker'
  worker_id?: string
  worker_env_name?: string
  python_version?: string
  runtime_scope?: 'shared' | 'private'
  runtime_kind?: 'python' | 'java' | 'go'
  runtime_locator?: string
  dependencies?: string[]
  execution_strategy?: ExecutionStrategy
  bound_worker_id?: string
  bound_worker_name?: string
  region?: string
  file_info?: FileInfo
  rule_info?: RuleInfo
  code_info?: CodeInfo
  task_count?: number
  last_execution?: string
  success_rate?: number
}

export interface FileInfo {
  language?: string
  entry_point?: string
  runtime_config?: RuntimeConfig
  environment_vars?: EnvironmentVars
  repository_id?: string
  repository_name?: string
  repository_url?: string
  ref?: string
  subdir?: string
  include_paths?: string[]
  resolved_revision?: string
}

export interface RuleInfo {
  engine: CrawlEngine
  region?: string
  require_render: boolean
  target_url: string
  url_pattern?: string
  callback_type: string
  request_method: string
  extraction_rules?: ExtractionRule[]
  data_schema?: DataSchema
  pagination_config?: PaginationConfig
  max_pages: number
  start_page: number
  request_delay: number
  retry_count: number
  timeout: number
  priority?: number
  dont_filter?: boolean
  headers?: HttpHeaders
  cookies?: HttpCookies
  proxy_config?: ProxyConfig
  anti_spider?: AntiSpiderConfig
  task_config?: TaskConfig
  resume_enabled?: boolean
  dedup_config?: RuleDedupConfig
}

export interface CodeInfo {
  language: string
  entry_point?: string
  runtime_config?: RuntimeConfig
  environment_vars?: EnvironmentVars
  documentation?: string
  repository_id?: string
  repository_name?: string
  repository_url?: string
  ref?: string
  subdir?: string
  include_paths?: string[]
  resolved_revision?: string
}

export interface ExtractionRule {
  desc: string
  type: 'css' | 'xpath' | 'regex'
  expr: string
  page_type?: 'list' | 'detail'
  attribute?: string
  transform?: string
}

export interface PaginationConfig {
  method:
    | 'none'
    | 'url_pattern'
    | 'url_param'
    | 'click_element'
    | 'js_click'
    | 'infinite_scroll'
    | 'javascript'
    | 'ajax'
  start_page?: number
  max_pages?: number
  next_page_rule?: ExtractionRule | { type: 'css' | 'xpath' | 'text'; expr: string } | string
  wait_after_click_ms?: number
  url_template?: string
  page_param?: string
  scroll_count?: number
  scroll_wait_ms?: number
  ajax_endpoint?: string
  ajax_params?: Record<string, unknown>
}

export interface DedupConfig {
  enabled?: boolean
  fields?: string[]
  scope?: 'project' | 'run'
  ttl_days?: number
  on_hit?: 'drop' | 'log'
}

export interface ProxyConfig {
  enabled?: boolean
  proxy_url?: string
  proxy_type?: 'http' | 'https' | 'socks4' | 'socks5'
  username?: string
  password?: string
  rotation?: boolean
  proxy_list?: string[]
}

export interface AntiSpiderConfig {
  enabled?: boolean
  user_agent_rotation?: boolean
  request_interval_range?: [number, number]
  random_delay?: boolean
  captcha_handling?: boolean
  cookie_persistence?: boolean
  ip_rotation?: boolean
  browser_fingerprint?: boolean
}

export interface TaskConfig {
  task_id_template?: string
  worker_id?: string
  queue_priority?: number
  retry_policy?: {
    max_retries: number
    retry_delay: number
    exponential_backoff: boolean
  }
  concurrency_limit?: number
}

export interface GitCredential {
  id: string
  name: string
  auth_type: 'token' | 'basic'
  username?: string | null
  host_scope: string
  has_secret: boolean
  created_at: string
  updated_at: string
}

export interface GitCredentialCreateRequest {
  name: string
  auth_type: 'token' | 'basic'
  username?: string
  secret: string
  host_scope: string
}

export interface GitCredentialUpdateRequest {
  name?: string
  auth_type?: 'token' | 'basic'
  username?: string
  secret?: string
  host_scope?: string
}
