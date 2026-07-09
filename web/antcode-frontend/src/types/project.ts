// 项目类型枚举
export type ProjectType = 'file' | 'rule' | 'code'
export type ProjectStatus = 'active' | 'inactive' | 'archived'
export type CrawlEngine = 'requests' | 'curl_cffi' | 'browser'

// 执行策略枚举
export type ExecutionStrategy = 'fixed' | 'specified' | 'auto' | 'prefer'

// 执行策略配置
export interface ExecutionStrategyConfig {
  strategy: ExecutionStrategy
  bound_worker_id?: string
  bound_worker_name?: string
}

// 执行策略选项（用于UI展示）
export const EXECUTION_STRATEGY_OPTIONS = [
  { value: 'fixed', label: '固定 Worker', description: '仅在绑定 Worker 执行，不可用时失败' },
  { value: 'specified', label: '指定 Worker', description: '在指定的 Worker 上执行' },
  { value: 'auto', label: '自动选择', description: '根据负载自动选择最优 Worker' },
  { value: 'prefer', label: '优先绑定 Worker', description: '优先使用绑定 Worker，不可用时自动选择其他 Worker' },
] as const

// 运行时配置类型
export interface RuntimeConfig {
  timeout?: number
  memory_limit?: number
  cpu_limit?: number
  max_retries?: number
  [key: string]: unknown
}

// 环境变量类型
export interface EnvironmentVars {
  [key: string]: string
}

// 数据模式类型
export interface DataSchema {
  [field: string]: {
    type: 'string' | 'number' | 'boolean' | 'array' | 'object'
    required?: boolean
    description?: string
  }
}

// HTTP头部类型
export interface HttpHeaders {
  [key: string]: string
}

// Cookie类型
export interface HttpCookies {
  [key: string]: string
}

// 基础项目接口
export interface Project {
  id: string  // public_id
  name: string
  type: ProjectType
  status: ProjectStatus
  description?: string
  tags?: string[]
  created_at: string
  updated_at: string
  created_by: string  // public_id
  created_by_username?: string
  // 环境信息
  env_location?: 'worker'
  worker_id?: string
  worker_env_name?: string
  python_version?: string
  runtime_scope?: 'shared' | 'private'
  runtime_kind?: 'python' | 'java' | 'go'
  runtime_locator?: string
  dependencies?: string[]

  // 执行策略配置
  execution_strategy?: ExecutionStrategy
  bound_worker_id?: string
  bound_worker_name?: string
  
  // 区域配置
  region?: string

  // 详情信息（从后端API返回）
  file_info?: FileInfo
  rule_info?: RuleInfo
  code_info?: CodeInfo

  // 统计信息
  task_count?: number
  last_execution?: string
  success_rate?: number
}

// 文件信息（从API返回）
export interface FileInfo {
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
  // 兼容旧版数据结构（后端某些历史 project 可能仍返回这些字段）
  source_type?: 'file' | 'git' | 'inline'
  git_url?: string
  git_branch?: string
  git_commit?: string
  git_subdir?: string
  git_credential_id?: string
}

// 规则信息（从API返回）
export interface RuleInfo {
  engine: CrawlEngine
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
}

// 代码信息（从API返回）
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
  // 兼容旧版数据结构
  version?: string
  content?: string
  source_type?: 'code' | 'git' | 'inline'
  git_url?: string
  git_branch?: string
  git_commit?: string
  git_subdir?: string
  git_credential_id?: string
}

// 文件项目详情
// 提取规则 - 根据v2.0.0 API文档更新
export interface ExtractionRule {
  desc: string  // 规则描述
  type: 'css' | 'xpath' | 'regex' | 'jsonpath'  // 规则类型
  expr: string  // 规则表达式
  page_type?: 'list' | 'detail'  // 页面类型（混合模式下使用）
  // 前端扩展字段（不发送到后端）
  attribute?: string  // 提取属性
  transform?: string  // 转换规则
}

// 分页配置 - S6/S7/S8 扩展
// S6: 新增 url_pattern / click_element / js_click 三种 method
// S7: URL 支持 {N} 数字占位符，N 就是起始页号
// S8: next_page_rule 允许字符串（自动识别 CSS/XPath）或 {type,expr} 结构化
export interface PaginationConfig {
  method:
    | 'none'
    | 'url_pattern'      // 用 URL 里 {}/{N}/{page} 占位符展开多页
    | 'url_param'        // ?page=N query 追加
    | 'click_element'    // HTTP 引擎，抓 next 元素的 href 递归
    | 'js_click'         // Playwright 引擎，点击 next 元素翻页
    | 'infinite_scroll'  // Playwright 引擎，滚动加载
    | 'javascript'       // 旧兼容：等价 infinite_scroll
    | 'ajax'             // 旧兼容：等价 infinite_scroll
  start_page?: number
  max_pages?: number
  // next 元素定位：字符串（旧）或 {type: 'css'|'xpath'|'text', expr: string}
  next_page_rule?: ExtractionRule | { type: 'css' | 'xpath' | 'text'; expr: string } | string
  wait_after_click_ms?: number  // js_click 点击后等待
  // url_pattern / url_param 用
  url_template?: string
  page_param?: string  // url_param 时的 query 参数名，缺省 'page'
  // infinite_scroll 用
  scroll_count?: number
  scroll_wait_ms?: number
  // 旧兼容
  ajax_endpoint?: string
  ajax_params?: Record<string, unknown>
}

// S5: 内容级去重配置
export interface DedupConfig {
  enabled?: boolean
  fields?: string[]                  // 参与哈希的字段（顺序敏感）
  scope?: 'project' | 'run'          // 去重范围
  ttl_days?: number                  // 去重集 TTL（0=不过期）
  on_hit?: 'drop' | 'log'            // 命中动作
}

// v2.0.0 新增配置类型
export interface ProxyConfig {
  enabled?: boolean  // 是否启用代理
  proxy_url?: string  // 代理地址
  proxy_type?: 'http' | 'https' | 'socks4' | 'socks5'  // 代理类型
  username?: string  // 代理用户名
  password?: string  // 代理密码
  rotation?: boolean  // 是否轮换代理
  proxy_list?: string[]  // 代理池
}

export interface AntiSpiderConfig {
  enabled?: boolean  // 是否启用反爬虫
  user_agent_rotation?: boolean  // 是否轮换User-Agent
  request_interval_range?: [number, number]  // 请求间隔范围(ms)
  random_delay?: boolean  // 是否随机延迟
  captcha_handling?: boolean  // 是否处理验证码
  cookie_persistence?: boolean  // 是否持久化Cookie
  ip_rotation?: boolean  // 是否轮换IP
  browser_fingerprint?: boolean  // 是否配置浏览器指纹
}

export interface TaskConfig {
  task_id_template?: string  // 任务ID模板
  worker_id?: string  // Worker ID
  queue_priority?: number  // 队列优先级
  retry_policy?: {
    max_retries: number
    retry_delay: number
    exponential_backoff: boolean
  }
  concurrency_limit?: number  // 并发限制
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


// 创建项目请求
export interface ProjectCreateRequest {
  name: string
  type: ProjectType
  description?: string
  tags?: string[]
  // 运行环境
  env_location?: 'worker'
  worker_id?: string
  runtime_scope: 'shared' | 'private'
  shared_runtime_key?: string
  python_version: string
  use_existing_env?: boolean
  existing_env_name?: string
  env_name?: string
  env_description?: string

  // 区域配置
  region?: string
  
  // 执行策略配置
  execution_strategy?: ExecutionStrategy
  bound_worker_id?: string
  
  // Git 来源字段
  repository_id?: string
  ref?: string
  subdir?: string
  include_paths?: string[] | string

  // 文件项目字段
  entry_point?: string
  runtime_config?: string | RuntimeConfig
  environment_vars?: string | EnvironmentVars
  dependencies?: string[]

  // 规则项目字段
  target_url?: string
  url_pattern?: string
  engine?: CrawlEngine
  request_delay?: number
  request_method?: string
  priority?: number
  retry_count?: number
  timeout?: number
  max_pages?: number
  start_page?: number
  callback_type?: 'list' | 'detail' | 'mixed'  // 回调类型：列表页/详情页/混合模式
  extraction_rules?: string  // JSON字符串格式
  pagination_config?: string  // JSON字符串格式
  headers?: Record<string, string>
  cookies?: Record<string, string>
  dont_filter?: boolean
  // v2.0.0 新增字段
  proxy_config?: string  // 代理配置JSON
  anti_spider?: string  // 反爬虫配置JSON
  task_config?: string  // 任务配置JSON
  data_schema?: string  // 数据结构定义JSON

  // 代码项目字段
  language?: string
  code_entry_point?: string
  documentation?: string
}

// 更新项目请求
export interface ProjectUpdateRequest {
  name?: string
  description?: string
  tags?: string[] | string
  status?: ProjectStatus
  type?: ProjectType
  
  // 区域配置
  region?: string
  
  // 执行策略配置
  execution_strategy?: ExecutionStrategy
  bound_worker_id?: string
  
  // 规则项目更新字段
  target_url?: string
  url_pattern?: string
  engine?: CrawlEngine
  request_method?: string
  request_delay?: number
  retry_count?: number
  timeout?: number
  priority?: number
  dont_filter?: boolean
  callback_type?: string
  extraction_rules?: string // JSON字符串格式
  pagination_config?: string // JSON字符串格式
  max_pages?: number
  start_page?: number
  headers?: Record<string, string> | string
  cookies?: Record<string, string> | string
  proxy_config?: string
  anti_spider?: string
  task_config?: string
  data_schema?: string
  
  // 代码项目更新字段
  language?: string
  code_entry_point?: string
  documentation?: string
  dependencies?: string[]
  repository_id?: string
  ref?: string
  subdir?: string
  include_paths?: string[] | string
  
  // 文件项目更新字段
  entry_point?: string
  runtime_config?: string | RuntimeConfig
  environment_vars?: string | EnvironmentVars
  env_location?: string
  worker_id?: string
  use_existing_env?: boolean
  existing_env_name?: string
  env_name?: string
  env_description?: string
}

export interface ProjectCodeConfigUpdateRequest {
  language?: string
  entry_point?: string
  documentation?: string
  runtime_config?: RuntimeConfig
  environment_vars?: EnvironmentVars
  // 兼容旧接口（后端仍接受这些字段）
  version?: string
  source_type?: 'code' | 'git' | 'inline'
  git_url?: string
  git_branch?: string
  git_commit?: string
  git_subdir?: string
  git_credential_id?: string
  code_content?: string
}

export interface ProjectFileConfigUpdateRequest {
  entry_point?: string
  runtime_config?: string | RuntimeConfig
  environment_vars?: string | EnvironmentVars
  // 兼容旧接口
  file?: File
  source_type?: 'file' | 'git' | 'inline'
  git_url?: string
  git_branch?: string
  git_commit?: string
  git_subdir?: string
  git_credential_id?: string
}

export interface ProjectSourcePayload {
  repository_id: string
  ref: string
  subdir: string
  entry_point: string
  include_paths: string[]
  runtime_config?: RuntimeConfig
}

// 项目列表查询参数
export interface ProjectListParams {
  page?: number
  size?: number
  type?: ProjectType
  status?: ProjectStatus
  tag?: string
  search?: string
  sort_by?: string
  sort_order?: 'asc' | 'desc'
  created_by?: string  // 创建者 public_id 筛选
  worker_id?: string   // Worker ID 筛选
}

// 项目统计信息
export interface ProjectStats {
  total_projects: number
  active_projects: number
  inactive_projects: number
  error_projects: number
  projects_by_type: {
    file: number
    rule: number
    code: number
  }
  recent_projects: Project[]
}

// 项目导出配置
export interface ProjectExportConfig {
  format: 'json' | 'yaml' | 'csv'
  include_tasks?: boolean
  include_logs?: boolean
  date_range?: {
    start: string
    end: string
  }
}
