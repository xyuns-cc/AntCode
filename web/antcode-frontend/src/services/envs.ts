import { BaseService } from './base'

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

class EnvService extends BaseService {
  constructor() {
    super('/api/v1')
  }

  async listPythonVersions(): Promise<string[]> {
    const data = await this.get<PythonVersionsResponse>('/runtimes/python/versions')
    return data.versions
  }

  async listInterpreters(): Promise<Array<{ version: string; python_bin: string; install_dir: string; source?: string }>> {
    return await this.get<Array<{ version: string; python_bin: string; install_dir: string; source?: string }>>('/runtimes/python/interpreters')
  }

  async listInstalledInterpreters(): Promise<Array<{ version: string; python_bin: string; install_dir: string; source?: string }>> {
    return this.listInterpreters()
  }

  async installInterpreter(version: string): Promise<void> {
    await this.post('/runtimes/python/interpreters', { version })
  }

  async registerLocalInterpreter(python_bin: string): Promise<void> {
    await this.post('/runtimes/python/interpreters/local', { python_bin })
  }

  async uninstallInterpreter(version: string, source: string = 'mise'): Promise<void> {
    await this.delete(`/runtimes/python/interpreters/${encodeURIComponent(version)}`, {
      params: { source },
    })
  }

  async listWorkerEnvs(workerId: string): Promise<Array<{ name: string; python_version: string }>> {
    return await this.get<Array<{ name: string; python_version: string }>>(
      `/runtimes/workers/${workerId}/runtimes`
    )
  }

  async listWorkerInterpreters(workerId: string): Promise<{ interpreters: Array<{ version: string; source?: string }>; total: number }> {
    const interpreters = await this.get<Array<{ version: string; source?: string }>>(
      `/runtimes/workers/${workerId}/interpreters`
    )
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
    const data = await this.get<{ items: VenvListItem[]; pagination: { page: number; size: number; total: number; pages: number } }>('/runtimes', { params })

    const { items, pagination } = data

    return {
      items,
      page: pagination.page,
      size: pagination.size,
      total: pagination.total,
      pages: pagination.pages,
    }
  }

  async batchDeleteVenvs(ids: string[]): Promise<{ total: number; deleted: number; failed: string[] }> {
    return await this.post<{ total: number; deleted: number; failed: string[] }>(
      '/runtimes/batch-delete',
      { ids }
    )
  }

  async listVenvPackagesById(runtime_id: string): Promise<Array<{ name: string; version: string }>> {
    const data = await this.get<{ runtime_id: string; packages: Array<{ name: string; version: string }> }>(
      `/runtimes/${runtime_id}/packages`
    )
    return data.packages
  }

  async listProjectVenvPackages(project_id: string): Promise<Array<{ name: string; version: string }>> {
    const data = await this.get<{ project_id: string; packages: Array<{ name: string; version: string }> }>(
      `/runtimes/projects/${project_id}/runtime/packages`
    )
    return data.packages
  }

  async createOrBindProjectVenv(project_id: string, payload: CreateOrBindVenvRequest): Promise<void> {
    await this.post(`/runtimes/projects/${project_id}/runtime`, payload)
  }

  async deleteProjectVenv(project_id: string): Promise<void> {
    await this.delete(`/runtimes/projects/${project_id}/runtime`)
  }

  async createSharedVenv(payload: {
    version: string
    shared_runtime_key?: string
    interpreter_source?: string
    python_bin?: string
  }): Promise<Record<string, unknown>> {
    return await this.post<Record<string, unknown>>('/runtimes', payload)
  }

  async deleteVenv(runtime_id: string, allowPrivate = false): Promise<void> {
    await this.delete(`/runtimes/${runtime_id}`, {
      params: { allow_private: allowPrivate },
    })
  }

  async updateSharedVenv(runtime_id: string, payload: { key?: string }): Promise<void> {
    await this.patch(`/runtimes/${runtime_id}`, payload)
  }

  async installPackagesToVenv(runtime_id: string, packages: string[]): Promise<void> {
    await this.post(`/runtimes/${runtime_id}/packages`, { packages })
  }

  async installPackagesToProjectVenv(project_id: string, packages: string[]): Promise<void> {
    await this.post(`/runtimes/projects/${project_id}/runtime/packages`, { packages })
  }
}

const envService = new EnvService()
export default envService
