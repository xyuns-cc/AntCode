import type {
  CrawlEngine,
  EnvironmentVars,
  ExecutionStrategy,
  Project,
  ProjectStatus,
  ProjectType,
  RuleDedupConfig,
  RuntimeConfig,
} from './projectModels'

export interface ProjectCreateRequest {
  name: string
  type: ProjectType
  description?: string
  tags?: string[]
  env_location?: 'worker'
  worker_id?: string
  runtime_scope: 'shared' | 'private'
  shared_runtime_key?: string
  python_version: string
  use_existing_env?: boolean
  existing_env_name?: string
  env_name?: string
  env_description?: string
  region?: string
  require_render?: boolean
  execution_strategy?: ExecutionStrategy
  bound_worker_id?: string
  repository_id?: string
  ref?: string
  subdir?: string
  include_paths?: string[] | string
  entry_point?: string
  runtime_config?: string | RuntimeConfig
  environment_vars?: string | EnvironmentVars
  dependencies?: string[]
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
  callback_type?: 'list' | 'detail' | 'mixed'
  extraction_rules?: string
  pagination_config?: string
  headers?: Record<string, string>
  cookies?: Record<string, string>
  dont_filter?: boolean
  proxy_config?: string
  anti_spider?: string
  task_config?: string
  data_schema?: string
  resume_enabled?: boolean
  dedup_config?: string | RuleDedupConfig
  language?: string
  code_entry_point?: string
  documentation?: string
}

export interface ProjectUpdateRequest {
  name?: string
  description?: string
  tags?: string[] | string
  status?: ProjectStatus
  type?: ProjectType
  region?: string
  require_render?: boolean
  execution_strategy?: ExecutionStrategy
  bound_worker_id?: string
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
  extraction_rules?: string
  pagination_config?: string
  max_pages?: number
  start_page?: number
  headers?: Record<string, string> | string
  cookies?: Record<string, string> | string
  proxy_config?: string
  anti_spider?: string
  task_config?: string
  resume_enabled?: boolean
  dedup_config?: string | RuleDedupConfig
  data_schema?: string
  language?: string
  code_entry_point?: string
  documentation?: string
  dependencies?: string[]
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
}

export interface ProjectFileConfigUpdateRequest {
  language?: string
  entry_point?: string
  runtime_config?: string | RuntimeConfig
  environment_vars?: string | EnvironmentVars
}

export interface ProjectSourcePayload {
  repository_id: string
  ref: string
  subdir: string
  include_paths: string[]
}

export interface ProjectSourceInfo extends ProjectSourcePayload {
  project_id: string
  repository_name: string
  repository_url: string
  resolved_commit?: string
}

export interface ProjectListParams {
  page?: number
  size?: number
  type?: ProjectType
  status?: ProjectStatus
  tag?: string
  search?: string
  sort_by?: string
  sort_order?: 'asc' | 'desc'
  created_by?: string
  worker_id?: string
}

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

export interface ProjectExportConfig {
  format: 'json' | 'yaml' | 'csv'
  include_tasks?: boolean
  include_logs?: boolean
  date_range?: {
    start: string
    end: string
  }
}
