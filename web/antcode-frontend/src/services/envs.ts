import apiClient from './api'
import type { ApiResponse } from '@/types'

export interface PythonVersionsResponse {
  versions: string[]
}

export type VenvScope = 'shared' | 'private'

export interface CreateOrBindVenvRequest {
  version: string
  runtime_scope: VenvScope
  shared_runtime_key?: string
  create_if_missing?: boolean
  interpreter_source?: string
  python_bin?: string
}

export interface VenvListItem {
  id: string
  runtime_kind?: 'python' | 'java' | 'go'
  scope: VenvScope
  key?: string | null
  version: string
  runtime_locator: string
  interpreter_version: string
  interpreter_source?: string
  python_bin: string
  install_dir: string
  created_by?: string | null
  created_by_username?: string | null
  created_at?: string | null
  updated_at?: string | null
  current_project_id?: string | null
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
    const res = await apiClient.get<PythonVersionsResponse>('/api/v1/runtimes/python/versions')
    return res.data.versions || []
  }

  async listInterpreters(): Promise<Array<{ version: string; python_bin: string; install_dir: string; source?: string }>> {
    const res = await apiClient.get<Array<{ version: string; python_bin: string; install_dir: string; source?: string; id?: number }>>('/api/v1/runtimes/python/interpreters')
    return res.data || []
  }

  async listInstalledInterpreters(): Promise<Array<{ version: string; python_bin: string; install_dir: string; source?: string }>> {
    return this.listInterpreters()
  }

  async installInterpreter(version: string): Promise<void> {
    await apiClient.post('/api/v1/runtimes/python/interpreters', { version })
  }

  async registerLocalInterpreter(python_bin: string): Promise<void> {
    await apiClient.post('/api/v1/runtimes/python/interpreters/local', { python_bin })
  }

  async uninstallInterpreter(version: string, source: string = 'mise'): Promise<void> {
    await apiClient.delete(`/api/v1/runtimes/python/interpreters/${encodeURIComponent(version)}`, {
      params: { source },
    })
  }

  async listWorkerEnvs(workerId: string): Promise<Array<{ name: string; python_version: string }>> {
    const res = await apiClient.get<ApiResponse<Array<{ name: string; python_version: string }>>>(
      `/api/v1/runtimes/workers/${workerId}/runtimes`
    )
    return res.data.data || []
  }

  async listWorkerInterpreters(workerId: string): Promise<{ interpreters: Array<{ version: string; source?: string }>; total: number }> {
    const res = await apiClient.get<ApiResponse<Array<{ version: string; source?: string }>>>(
      `/api/v1/runtimes/workers/${workerId}/interpreters`
    )
    const interpreters = res.data.data || []
    return { interpreters, total: interpreters.length }
  }

  async listVenvs(params: {
    scope?: VenvScope
    project_id?: string
    q?: string
    page?: number
    size?: number
    include_packages?: boolean
    limit_packages?: number
    interpreter_source?: string
    worker_id?: string
  }): Promise<PaginatedVenvs> {
    const res = await apiClient.get<{
      success: boolean
      data: VenvListItem[]
      pagination: { page: number; size: number; total: number; pages: number }
    }>('/api/v1/runtimes', { params })

    return {
      items: res.data.data || [],
      page: res.data.pagination?.page || 1,
      size: res.data.pagination?.size || 20,
      total: res.data.pagination?.total || 0,
      pages: res.data.pagination?.pages || 1,
    }
  }

  async batchDeleteVenvs(ids: string[]): Promise<{ total: number; deleted: number; failed: string[] }> {
    const res = await apiClient.post<{ total: number; deleted: number; failed: string[] }>(
      '/api/v1/runtimes/batch-delete',
      { ids }
    )
    return res.data
  }

  async listVenvPackagesById(runtime_id: string): Promise<Array<{ name: string; version: string }>> {
    const res = await apiClient.get<{ runtime_id: string; packages: Array<{ name: string; version: string }> }>(
      `/api/v1/runtimes/${runtime_id}/packages`
    )
    return res.data.packages || []
  }

  async listProjectVenvPackages(project_id: string): Promise<Array<{ name: string; version: string }>> {
    const res = await apiClient.get<{ project_id: string; packages: Array<{ name: string; version: string }> }>(
      `/api/v1/runtimes/projects/${project_id}/runtime/packages`
    )
    return res.data.packages || []
  }

  async createOrBindProjectVenv(project_id: string, payload: CreateOrBindVenvRequest): Promise<void> {
    await apiClient.post(`/api/v1/runtimes/projects/${project_id}/runtime`, payload)
  }

  async deleteProjectVenv(project_id: string): Promise<void> {
    await apiClient.delete(`/api/v1/runtimes/projects/${project_id}/runtime`)
  }

  async createSharedVenv(payload: {
    version: string
    shared_runtime_key?: string
    interpreter_source?: string
    python_bin?: string
  }): Promise<Record<string, unknown>> {
    const res = await apiClient.post<Record<string, unknown>>('/api/v1/runtimes', payload)
    return res.data
  }

  async deleteVenv(runtime_id: string, allowPrivate = false): Promise<void> {
    await apiClient.delete(`/api/v1/runtimes/${runtime_id}`, {
      params: { allow_private: allowPrivate },
    })
  }

  async updateSharedVenv(runtime_id: string, payload: { key?: string }): Promise<void> {
    await apiClient.patch(`/api/v1/runtimes/${runtime_id}`, payload)
  }

  async installPackagesToVenv(runtime_id: string, packages: string[]): Promise<void> {
    await apiClient.post(`/api/v1/runtimes/${runtime_id}/packages`, { packages })
  }

  async installPackagesToProjectVenv(project_id: string, packages: string[]): Promise<void> {
    await apiClient.post(`/api/v1/runtimes/projects/${project_id}/runtime/packages`, { packages })
  }
}

const envService = new EnvService()
export default envService
