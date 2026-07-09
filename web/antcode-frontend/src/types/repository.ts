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
  runtime_scope: 'shared' | 'private'
  runtime_kind: 'python'
  python_version?: string
  worker_id?: string
  execution_strategy: 'auto' | 'fixed' | 'specified' | 'prefer'
  bound_worker_id?: string
}

export interface ImportProjectsPayload {
  projects: ProjectImportItem[]
}

export interface ImportProjectsResult {
  created: string[]
}
