import apiClient from './api'

export interface PythonVersionsResponse {
  versions: string[]
}

export type VenvScope = 'shared' | 'private'

export interface CreateOrBindVenvRequest {
  version: string
  venv_scope: VenvScope
  shared_venv_key?: string
  create_if_missing?: boolean
  interpreter_source?: 'mise' | 'local'
  python_bin?: string
}

export interface VenvListItem {
  id: number
  scope: VenvScope
  key?: string | null
  version: string
  venv_path: string
  interpreter_version: string
  python_bin: string
  install_dir: string
  created_by?: number | null
  created_by_username?: string | null
  created_at?: string | null
  updated_at?: string | null
  packages?: Array<{ name: string; version: string }>
}

export interface PaginatedVenvs {
  items: VenvListItem[]
  page: number
  size: number
  total: number
  pages: number
}

class EnvService {
  async listPythonVersions(): Promise<string[]> {
    const res = await apiClient.get<PythonVersionsResponse>('/api/v1/envs/python/versions')
    return res.data.versions || []
  }

  async listInterpreters(): Promise<Array<{ version: string; python_bin: string; install_dir: string }>> {
    const res = await apiClient.get<Array<{ version: string; python_bin: string; install_dir: string; source?: string; id?: number }>>('/api/v1/envs/python/interpreters')
    return res.data || []
  }

  async installInterpreter(version: string): Promise<void> {
    await apiClient.post('/api/v1/envs/python/interpreters', { version })
  }

  async registerLocalInterpreter(python_bin: string): Promise<void> {
    await apiClient.post('/api/v1/envs/python/interpreters/local', { python_bin })
  }

  async uninstallInterpreter(version: string, source: 'mise' | 'local' = 'mise'): Promise<void> {
    await apiClient.delete(`/api/v1/envs/python/interpreters/${encodeURIComponent(version)}`, { params: { source } })
  }

  async listVenvs(params: {
    scope?: VenvScope
    project_id?: number
    q?: string
    page?: number
    size?: number
    include_packages?: boolean
    limit_packages?: number
    interpreter_source?: 'mise' | 'local'
  }): Promise<PaginatedVenvs> {
    const res = await apiClient.get<{
      success: boolean
      data: VenvListItem[]
      pagination: { page: number; size: number; total: number; pages: number }
    }>('/api/v1/envs/venvs', { params })
    return {
      items: res.data.data || [],
      page: res.data.pagination?.page || 1,
      size: res.data.pagination?.size || 20,
      total: res.data.pagination?.total || 0,
      pages: res.data.pagination?.pages || 1,
    }
  }

  async batchDeleteVenvs(ids: number[]): Promise<{ total: number; deleted: number; failed: number[] }> {
    const res = await apiClient.post<{ total: number; deleted: number; failed: number[] }>('/api/v1/envs/venvs/batch-delete', { ids })
    return res.data
  }

  async listVenvPackagesById(venv_id: number): Promise<Array<{ name: string; version: string }>> {
    const res = await apiClient.get<{ venv_id: number; packages: Array<{ name: string; version: string }> }>(`/api/v1/envs/venvs/${venv_id}/packages`)
    return res.data.packages || []
  }

  async listProjectVenvPackages(project_id: number): Promise<Array<{ name: string; version: string }>> {
    const res = await apiClient.get<{ project_id: number; packages: Array<{ name: string; version: string }> }>(`/api/v1/envs/projects/${project_id}/venv/packages`)
    return res.data.packages || []
  }

  async createOrBindProjectVenv(project_id: number, payload: CreateOrBindVenvRequest): Promise<void> {
    await apiClient.post(`/api/v1/envs/projects/${project_id}/venv`, payload)
  }

  async deleteProjectVenv(project_id: number): Promise<void> {
    await apiClient.delete(`/api/v1/envs/projects/${project_id}/venv`)
  }

  async createSharedVenv(payload: { version: string; shared_venv_key?: string; interpreter_source?: 'mise' | 'local'; python_bin?: string }): Promise<any> {
    const res = await apiClient.post('/api/v1/envs/venvs', payload)
    return res.data
  }

  async deleteVenv(venv_id: number, allowPrivate = false): Promise<void> {
    await apiClient.delete(`/api/v1/envs/venvs/${venv_id}`, { params: { allow_private: allowPrivate } })
  }

  async updateSharedVenv(venv_id: number, payload: { key?: string }): Promise<void> {
    await apiClient.patch(`/api/v1/envs/venvs/${venv_id}`, payload)
  }

  async installPackagesToVenv(venv_id: number, packages: string[]): Promise<void> {
    await apiClient.post(`/api/v1/envs/venvs/${venv_id}/packages`, { packages })
  }

  async installPackagesToProjectVenv(project_id: number, packages: string[]): Promise<void> {
    await apiClient.post(`/api/v1/envs/projects/${project_id}/venv/packages`, { packages })
  }
}

const envService = new EnvService()
export default envService
