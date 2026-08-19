export interface GitRepository {
  id: string
  name: string
  url: string
  default_ref: string
  credential_id?: string | null
  enabled: boolean
  last_scan_status?: string | null
  last_scan_error?: string | null
  last_scan_result?: RepositoryCandidate[] | null
  last_scanned_at?: string | null
  created_at: string
  updated_at: string
}

export interface RepositoryCandidate {
  subdir: string
  entry_point: string
  markers: string[]
}

export interface RepositoryScanResult {
  repository_id: string
  ref: string
  candidates: RepositoryCandidate[]
}

export interface RepositoryCreatePayload {
  name: string
  url: string
  default_ref: string
  credential_id?: string
}

// PUT /repositories/{id} 的 RepositoryUpdateRequest 每个字段都是可选的，
// 未传的字段服务端不动（domain/schemas/repository.py::RepositoryUpdateRequest）。
// credential_id 必须能显式传 null 才解得开"已选凭证"，Partial 的 undefined 会被
// model_config extra=forbid 之外的 exclude_unset 语义当成"没传"。
export type RepositoryUpdatePayload = Omit<Partial<RepositoryCreatePayload>, 'credential_id'> & {
  credential_id?: string | null
}

export interface ProjectImportItem {
  repository_id: string
  ref: string
  subdir: string
  entry_point: string
  include_paths: string[]
  name: string
  description?: string
  tags: string[]
  dependencies?: string[]
  runtime_scope: 'private'
  runtime_kind: 'python'
  python_version: string
  worker_id: string
  execution_strategy: 'fixed'
  bound_worker_id: string
}

// 扫描导入表单里真正注册了 Form.Item 的字段只有这两个（ScanImportDrawer
// 的 buildCandidateColumns）。antd 的 validateFields() 只回传已注册字段，
// 所以表单值类型必须与注册项一一对应，不能声明成整份 ProjectImportItem，
// 否则类型系统会为"表单能带回全部字段"这一错误假设背书。
export interface ProjectImportEditable {
  name: string
  include_paths: string[]
}

export interface ProjectImportFormValues {
  worker_id?: string
  python_version?: string
  projects: Record<string, ProjectImportEditable>
}

export interface ImportProjectsPayload {
  projects: ProjectImportItem[]
}

export interface ImportProjectsResult {
  created: string[]
}
