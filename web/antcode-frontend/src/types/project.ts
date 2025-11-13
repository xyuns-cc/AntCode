// 项目类型枚举
export type ProjectType = 'file' | 'rule' | 'code'
export type ProjectStatus = 'active' | 'inactive' | 'error'

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

// 提取规则类型
export interface ExtractionRules {
  [selector: string]: {
    type: 'text' | 'attr' | 'html'
    attr?: string
    required?: boolean
    default?: string
  }
}

// 数据模式类型
export interface DataSchema {
  [field: string]: {
    type: 'string' | 'number' | 'boolean' | 'array' | 'object'
    required?: boolean
    description?: string
  }
}

// 分页配置类型
export interface PaginationConfig {
  type: 'url' | 'form' | 'ajax'
  selector?: string
  pattern?: string
  max_pages?: number
  start_page?: number
}

// HTTP头部类型
export interface HttpHeaders {
  [key: string]: string
}

// Cookie类型
export interface HttpCookies {
  [key: string]: string
}

// 代理配置类型
export interface ProxyConfig {
  host: string
  port: number
  username?: string
  password?: string
  type?: 'http' | 'https' | 'socks4' | 'socks5'
}

// 基础项目接口
export interface Project {
  id: number
  name: string
  type: ProjectType
  status: ProjectStatus
  description?: string
  tags?: string[]
  created_at: string
  updated_at: string
  created_by: number
  created_by_username?: string
  // 环境信息
  python_version?: string
  venv_scope?: 'shared' | 'private'
  venv_path?: string

  // 详情信息（从后端API返回）
  file_info?: FileInfo
  rule_info?: RuleInfo
  code_info?: CodeInfo

  // 关联数据（旧版本兼容）
  project_file?: ProjectFile
  project_rule?: ProjectRule
  project_code?: ProjectCode

  // 统计信息
  task_count?: number
  last_execution?: string
  success_rate?: number
}

// 文件信息（从API返回）
export interface FileInfo {
  original_name: string
  file_size: number
  file_hash: string
  file_path?: string
  file_type?: string
  entry_point?: string
  runtime_config?: RuntimeConfig
  environment_vars?: EnvironmentVars
}

export interface ProjectFileContent {
  name: string
  path: string
  size: number
  modified_time: number
  mime_type: string
  is_text: boolean
  content?: string
  encoding?: string
  error?: string
  too_large?: boolean
  binary?: boolean
}

// 规则信息（从API返回）
export interface RuleInfo {
  engine: string
  target_url: string
  url_pattern?: string
  callback_type: string
  request_method: string
  extraction_rules?: ExtractionRules
  list_selectors?: ExtractionRules
  detail_selectors?: ExtractionRules
  data_schema?: DataSchema
  pagination_config?: PaginationConfig
  pagination_type?: string
  pagination_rule?: string
  max_pages: number
  start_page: number
  request_delay: number
  retry_count: number
  timeout: number
  headers?: HttpHeaders
  cookies?: HttpCookies
  proxy_config?: ProxyConfig
}

// 代码信息（从API返回）
export interface CodeInfo {
  content: string
  language: string
  version: string
  content_hash: string
  entry_point?: string
  runtime_config?: RuntimeConfig
  environment_vars?: EnvironmentVars
}

// 文件项目详情
export interface ProjectFile {
  id: number
  project_id: number
  original_name: string
  file_path: string
  file_size: number
  file_hash: string
  dependencies?: string[]
  created_at: string
}

// 规则项目详情
export interface ProjectRule {
  id: number
  project_id: number
  target_url: string
  detail_selectors: ExtractionRule[]
  pagination_config?: PaginationConfig
  request_method: string
  headers?: Record<string, string>
  cookies?: Record<string, string>
  callback_type: string
  priority: number
  dont_filter: boolean
  created_at: string
}

// 代码项目详情
export interface ProjectCode {
  id: number
  project_id: number
  language: string
  code_content: string
  entry_point?: string
  dependencies?: string[]
  created_at: string
}

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

// 分页配置 - 根据新版API文档优化
export interface PaginationConfig {
  method: 'none' | 'url_param' | 'javascript' | 'ajax' | 'infinite_scroll'  // 分页方式
  start_page?: number  // 起始页码
  max_pages?: number  // 最大页数
  next_page_rule?: ExtractionRule  // 下一页规则
  wait_after_click_ms?: number  // 点击后等待时间
  // URL参数方式的配置
  url_template?: string  // URL模板，如 /page/{page}
  // AJAX方式的配置
  ajax_endpoint?: string  // AJAX请求地址
  ajax_params?: Record<string, any>  // AJAX请求参数
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
  browser_fingerprint?: boolean  // 是否模拟浏览器指纹
}

export interface TaskConfig {
  task_id_template?: string  // 任务ID模板
  worker_id?: string  // 工作节点ID
  queue_priority?: number  // 队列优先级
  retry_policy?: {
    max_retries: number
    retry_delay: number
    exponential_backoff: boolean
  }
  concurrency_limit?: number  // 并发限制
}

// 创建项目请求
export interface ProjectCreateRequest {
  name: string
  type: ProjectType
  description?: string
  tags?: string[]
  // 环境必填
  venv_scope: 'shared' | 'private'
  python_version: string
  shared_venv_key?: string

  // 文件项目字段
  file?: File
  additionalFiles?: File[] // 新增：附加文件列表
  entry_point?: string // 新增：入口文件
  runtime_config?: string | RuntimeConfig // 新增：运行时配置
  environment_vars?: string | EnvironmentVars // 新增：环境变量
  dependencies?: string[]

  // 规则项目字段
  target_url?: string
  url_pattern?: string
  engine?: string
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
  version?: string
  code_content?: string
  code_file?: File
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
  
  // 规则项目更新字段
  target_url?: string
  url_pattern?: string
  engine?: string
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
  code_content?: string
  language?: string
  version?: string
  code_entry_point?: string
  documentation?: string
  dependencies?: string[]
  
  // 文件项目更新字段
  entry_point?: string
  runtime_config?: string
  environment_vars?: string
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
  created_by?: number  // 新增：创建者ID筛选
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

// 项目导入配置
export interface ProjectImportConfig {
  file: File
  overwrite_existing?: boolean
  import_tasks?: boolean
  import_logs?: boolean
}
