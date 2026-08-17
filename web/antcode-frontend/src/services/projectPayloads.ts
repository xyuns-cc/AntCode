import type {
  ProjectCreateRequest,
  ProjectFileConfigUpdateRequest,
  ProjectUpdateRequest,
} from '@/types'

type RuleFieldData = Partial<ProjectCreateRequest & ProjectUpdateRequest>
type RuleField = keyof RuleFieldData

const RULE_TEXT_FIELDS = [
  'target_url',
  'url_pattern',
  'engine',
  'extraction_rules',
  'pagination_config',
  'request_method',
  'callback_type',
  'region',
] as const satisfies readonly RuleField[]

const RULE_NUMBER_FIELDS = [
  'request_delay',
  'priority',
  'retry_count',
  'timeout',
] as const satisfies readonly RuleField[]

const RULE_JSON_FIELDS = [
  'proxy_config',
  'anti_spider',
  'task_config',
  'data_schema',
  'dedup_config',
] as const satisfies readonly RuleField[]

const RULE_UPDATE_FIELDS = [
  'engine',
  'region',
  'require_render',
  'target_url',
  'url_pattern',
  'callback_type',
  'request_method',
  'extraction_rules',
  'pagination_config',
  'max_pages',
  'start_page',
  'request_delay',
  'priority',
  'dont_filter',
  'headers',
  'cookies',
  'proxy_config',
  'anti_spider',
  'task_config',
  'data_schema',
  'retry_count',
  'timeout',
  'resume_enabled',
  'dedup_config',
] as const satisfies readonly (keyof ProjectUpdateRequest)[]

const RULE_UPDATE_JSON_FIELDS = [
  'headers',
  'cookies',
  'task_config',
  'proxy_config',
  'anti_spider',
  'data_schema',
  'dedup_config',
] as const

const RULE_UPDATE_NUMBER_FIELDS = [
  'max_pages',
  'start_page',
  'request_delay',
  'priority',
  'retry_count',
  'timeout',
] as const

function appendJsonField(
  formData: FormData,
  field: string,
  value: string | object | undefined,
  emptyObjectWhenBlank = false
): void {
  if (value === undefined) return
  if (typeof value !== 'string') {
    formData.append(field, JSON.stringify(value))
    return
  }
  const normalized = value.trim()
  formData.append(field, !normalized && emptyObjectWhenBlank ? '{}' : value)
}

function appendRepositorySourceFields(formData: FormData, data: ProjectCreateRequest): void {
  if (data.repository_id) formData.append('repository_id', data.repository_id)
  if (data.ref) formData.append('ref', data.ref)
  if (data.subdir) formData.append('subdir', data.subdir)
  // include_paths 是后端唯一的 list[str] 表单字段，Starlette 按「重复同名键」收集列表，
  // 所以每个路径必须各占一个表单条目。整体 JSON.stringify 会被收成含单个字面量
  // 元素的列表（[] -> ['[]']），后端随后对着名为 "[]" 的目录打包源码而必然失败。
  // 线格式由 contracts/http/project_create_form.json 单点定义，两侧各有测试绑定。
  for (const path of data.include_paths ?? []) formData.append('include_paths', path)
}

function appendBaseFields(formData: FormData, data: ProjectCreateRequest): void {
  formData.append('name', data.name)
  formData.append('type', data.type)
  if (data.description) formData.append('description', data.description)
  if (data.tags) {
    formData.append('tags', Array.isArray(data.tags) ? data.tags.join(',') : data.tags)
  }
}

function appendEnvironmentFields(formData: FormData, data: ProjectCreateRequest): void {
  formData.append('runtime_scope', data.runtime_scope)
  formData.append('python_version', data.python_version)
  if (data.shared_runtime_key) formData.append('shared_runtime_key', data.shared_runtime_key)
  if (data.env_location) formData.append('env_location', data.env_location)
  if (data.worker_id) formData.append('worker_id', data.worker_id)
  if (data.use_existing_env !== undefined) {
    formData.append('use_existing_env', String(data.use_existing_env))
  }
  if (data.existing_env_name) formData.append('existing_env_name', data.existing_env_name)
  if (data.env_name) formData.append('env_name', data.env_name)
  if (data.env_description) formData.append('env_description', data.env_description)
}

function appendDependencies(formData: FormData, dependencies: string[] | undefined): void {
  if (dependencies) formData.append('dependencies', JSON.stringify(dependencies))
}

function appendFileFields(formData: FormData, data: ProjectCreateRequest): void {
  appendRepositorySourceFields(formData, data)
  if (data.language) formData.append('language', data.language)
  if (data.entry_point) formData.append('entry_point', data.entry_point)
  appendJsonField(formData, 'runtime_config', data.runtime_config, true)
  appendJsonField(formData, 'environment_vars', data.environment_vars, true)
  appendDependencies(formData, data.dependencies)
}

function appendCodeFields(formData: FormData, data: ProjectCreateRequest): void {
  appendRepositorySourceFields(formData, data)
  if (data.language) formData.append('language', data.language)
  if (data.code_entry_point) {
    formData.append('code_entry_point', data.code_entry_point)
    formData.append('entry_point', data.code_entry_point)
  }
  if (data.documentation) formData.append('documentation', data.documentation)
  appendDependencies(formData, data.dependencies)
}

function appendRuleFields(formData: FormData, data: RuleFieldData): void {
  RULE_TEXT_FIELDS.forEach((field) => {
    const value = data[field]
    if (value) formData.append(field, String(value))
  })
  RULE_NUMBER_FIELDS.forEach((field) => {
    const value = data[field]
    if (value !== undefined) formData.append(field, String(value))
  })
  RULE_JSON_FIELDS.forEach((field) => appendJsonField(formData, field, data[field]))
  if (data.headers) formData.append('headers', JSON.stringify(data.headers))
  if (data.cookies) formData.append('cookies', JSON.stringify(data.cookies))
  if (data.resume_enabled !== undefined && data.resume_enabled !== null) {
    formData.append('resume_enabled', String(Boolean(data.resume_enabled)))
  }
  if (data.dont_filter !== undefined) formData.append('dont_filter', String(data.dont_filter))
  if (data.require_render !== undefined) {
    formData.append('require_render', String(data.require_render))
  }
  appendDependencies(formData, data.dependencies)
}

export function createProjectFormData(data: ProjectCreateRequest): FormData {
  const formData = new FormData()
  appendBaseFields(formData, data)
  appendEnvironmentFields(formData, data)
  if (data.type === 'file') appendFileFields(formData, data)
  if (data.type === 'rule') appendRuleFields(formData, data)
  if (data.type === 'code') appendCodeFields(formData, data)
  return formData
}

export function createFileConfigFormData(data: ProjectFileConfigUpdateRequest): FormData {
  const formData = new FormData()
  if (data.language) formData.append('language', data.language)
  if (data.entry_point) formData.append('entry_point', data.entry_point)
  appendJsonField(formData, 'runtime_config', data.runtime_config, true)
  appendJsonField(formData, 'environment_vars', data.environment_vars, true)
  return formData
}

function selectRuleUpdateFields(data: Partial<ProjectUpdateRequest>): Record<string, unknown> {
  const payload: Record<string, unknown> = {}
  RULE_UPDATE_FIELDS.forEach((field) => {
    const value = data[field]
    const clearsRegion = field === 'region' && Object.prototype.hasOwnProperty.call(data, field)
    if (value === undefined && !clearsRegion) return
    payload[field] = clearsRegion && value === undefined ? null : value
  })
  return payload
}

function parseJsonValue(value: unknown, field: string, emptyObjectWhenBlank = false): unknown {
  if (value === undefined || value === null || typeof value !== 'string') return value
  if (!value.trim()) return emptyObjectWhenBlank ? {} : undefined
  try {
    return JSON.parse(value)
  } catch {
    throw new Error(`${field} JSON解析失败`)
  }
}

function normalizeJsonFields(payload: Record<string, unknown>): void {
  if (payload.extraction_rules !== undefined) {
    const parsed = parseJsonValue(payload.extraction_rules, 'extraction_rules')
    if (!Array.isArray(parsed)) throw new Error('extraction_rules 必须是数组')
    payload.extraction_rules = parsed
  }
  if (payload.pagination_config !== undefined) {
    const parsed = parseJsonValue(payload.pagination_config, 'pagination_config')
    if (parsed && typeof parsed !== 'object') throw new Error('pagination_config 必须是对象')
    payload.pagination_config = parsed
  }
  RULE_UPDATE_JSON_FIELDS.forEach((field) => {
    if (payload[field] !== undefined) payload[field] = parseJsonValue(payload[field], field, true)
  })
}

function normalizeNumberFields(payload: Record<string, unknown>): void {
  RULE_UPDATE_NUMBER_FIELDS.forEach((field) => {
    const value = payload[field]
    if (typeof value !== 'string') return
    const parsed = Number(value)
    if (Number.isNaN(parsed)) throw new Error(`${field} 必须是数字`)
    payload[field] = parsed
  })
}

function normalizeBooleanField(payload: Record<string, unknown>, field: string): void {
  if (typeof payload[field] === 'string') payload[field] = payload[field] === 'true'
}

export function buildRuleConfigPayload(
  data: Partial<ProjectUpdateRequest>
): Record<string, unknown> {
  const payload = selectRuleUpdateFields(data)
  normalizeJsonFields(payload)
  normalizeNumberFields(payload)
  normalizeBooleanField(payload, 'dont_filter')
  normalizeBooleanField(payload, 'require_render')
  return payload
}
