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
  interpreter_source?: string
  python_bin?: string
}

export interface VenvListItem {
  id: string  // public_id
  scope: VenvScope
  key?: string | null
  version: string
  venv_path: string
  interpreter_version: string
  interpreter_source?: string
  python_bin: string
  install_dir: string
  created_by?: string | null  // public_id
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

// 节点虚拟环境
export interface NodeEnvItem {
  name: string
  path: string
  python_version: string
  python_bin: string
  created_at?: string
  packages_count?: number
}

// 节点解释器
export interface NodeInterpreter {
  version: string
  install_dir?: string
  python_bin: string
  source: string
  is_available?: boolean
  created_at?: string
  updated_at?: string
}

// 节点Python版本信息
export interface NodePythonVersions {
  installed: NodeInterpreter[]
  available: string[]
  all_interpreters: NodeInterpreter[]
  platform: {
    os_type: string
    os_version: string
    python_version: string
    machine: string
    mise_available: boolean
  }
}

class EnvService {
  async listPythonVersions(): Promise<string[]> {
    const res = await apiClient.get<PythonVersionsResponse>('/api/v1/envs/python/versions')
    return res.data.versions || []
  }

  async listInterpreters(): Promise<Array<{ version: string; python_bin: string; install_dir: string; source?: string }>> {
    const res = await apiClient.get<Array<{ version: string; python_bin: string; install_dir: string; source?: string; id?: number }>>('/api/v1/envs/python/interpreters')
    return res.data || []
  }

  // 获取已安装的解释器列表（用于项目创建时选择）
  async listInstalledInterpreters(): Promise<Array<{ version: string; python_bin: string; install_dir: string; source?: string }>> {
    return this.listInterpreters()
  }

  async installInterpreter(version: string): Promise<void> {
    await apiClient.post('/api/v1/envs/python/interpreters', { version })
  }

  async registerLocalInterpreter(python_bin: string): Promise<void> {
    await apiClient.post('/api/v1/envs/python/interpreters/local', { python_bin })
  }

  async uninstallInterpreter(version: string, source: string = 'mise'): Promise<void> {
    await apiClient.delete(`/api/v1/envs/python/interpreters/${encodeURIComponent(version)}`, { params: { source } })
  }

  // ========== 节点环境管理 API ==========

  // 获取节点虚拟环境列表
  async listNodeEnvs(nodeId: string): Promise<NodeEnvItem[]> {
    const res = await apiClient.get<{ data: { envs: NodeEnvItem[] } }>(`/api/v1/nodes/${nodeId}/envs`)
    return res.data.data?.envs || []
  }

  // 在节点上创建虚拟环境
  async createNodeEnv(nodeId: string, data: { name: string; python_version?: string; packages?: string[] }): Promise<NodeEnvItem> {
    const res = await apiClient.post<{ data: NodeEnvItem }>(`/api/v1/nodes/${nodeId}/envs`, data)
    return res.data.data
  }

  // 获取节点虚拟环境详情
  async getNodeEnv(nodeId: string, envName: string): Promise<NodeEnvItem> {
    const res = await apiClient.get<{ data: NodeEnvItem }>(`/api/v1/nodes/${nodeId}/envs/${envName}`)
    return res.data.data
  }

  // 编辑节点虚拟环境
  async updateNodeEnv(nodeId: string, envName: string, data: { key?: string; description?: string }): Promise<NodeEnvItem> {
    const res = await apiClient.patch<{ data: NodeEnvItem }>(`/api/v1/nodes/${nodeId}/envs/${envName}`, data)
    return res.data.data
  }

  // 删除节点虚拟环境
  async deleteNodeEnv(nodeId: string, envName: string): Promise<void> {
    await apiClient.delete(`/api/v1/nodes/${nodeId}/envs/${envName}`)
  }

  // 获取节点虚拟环境包列表
  async listNodeEnvPackages(nodeId: string, envName: string): Promise<Array<{ name: string; version: string }>> {
    const res = await apiClient.get<{ data: { packages: Array<{ name: string; version: string }> } }>(`/api/v1/nodes/${nodeId}/envs/${envName}/packages`)
    return res.data.data?.packages || []
  }

  // 安装包到节点虚拟环境
  async installNodeEnvPackages(nodeId: string, envName: string, packages: string[], upgrade?: boolean): Promise<void> {
    await apiClient.post(`/api/v1/nodes/${nodeId}/envs/${envName}/packages`, { packages, upgrade })
  }

  // 从节点虚拟环境卸载包
  async uninstallNodeEnvPackages(nodeId: string, envName: string, packages: string[]): Promise<void> {
    await apiClient.delete(`/api/v1/nodes/${nodeId}/envs/${envName}/packages`, { data: { packages } })
  }

  // 获取节点解释器列表
  async listNodeInterpreters(nodeId: string): Promise<{ interpreters: NodeInterpreter[]; total: number }> {
    const res = await apiClient.get<{ data: { interpreters: NodeInterpreter[]; total: number } }>(`/api/v1/nodes/${nodeId}/interpreters`)
    return res.data.data || { interpreters: [], total: 0 }
  }

  // 在节点上注册本地解释器
  async registerNodeInterpreter(nodeId: string, pythonBin: string): Promise<void> {
    await apiClient.post(`/api/v1/nodes/${nodeId}/interpreters/local`, { python_bin: pythonBin })
  }

  // 在节点上取消注册解释器
  async unregisterNodeInterpreter(nodeId: string, version: string, source: string = 'local'): Promise<void> {
    await apiClient.delete(`/api/v1/nodes/${nodeId}/interpreters/${encodeURIComponent(version)}`, { params: { source } })
  }

  // 获取节点Python版本信息
  async getNodePythonVersions(nodeId: string): Promise<NodePythonVersions> {
    const res = await apiClient.get<{ data: NodePythonVersions }>(`/api/v1/nodes/${nodeId}/python-versions`)
    return res.data.data
  }

  // 在节点上安装Python版本
  async installNodePythonVersion(nodeId: string, version: string): Promise<void> {
    await apiClient.post(`/api/v1/nodes/${nodeId}/python-versions/${version}/install`)
  }

  // 获取节点平台信息
  async getNodePlatform(nodeId: string): Promise<{ os_type: string; os_version: string; python_version: string; machine: string; mise_available: boolean }> {
    const res = await apiClient.get<{ data: { os_type: string; os_version: string; python_version: string; machine: string; mise_available: boolean } }>(
      `/api/v1/nodes/${nodeId}/platform`
    )
    return res.data.data
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
    node_id?: string  // 节点ID筛选
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

  async batchDeleteVenvs(ids: string[]): Promise<{ total: number; deleted: number; failed: string[] }> {
    const res = await apiClient.post<{ total: number; deleted: number; failed: string[] }>('/api/v1/envs/venvs/batch-delete', { ids })
    return res.data
  }

  async listVenvPackagesById(venv_id: string): Promise<Array<{ name: string; version: string }>> {
    const res = await apiClient.get<{ venv_id: string; packages: Array<{ name: string; version: string }> }>(`/api/v1/envs/venvs/${venv_id}/packages`)
    return res.data.packages || []
  }

  async listProjectVenvPackages(project_id: string): Promise<Array<{ name: string; version: string }>> {
    const res = await apiClient.get<{ project_id: string; packages: Array<{ name: string; version: string }> }>(`/api/v1/envs/projects/${project_id}/venv/packages`)
    return res.data.packages || []
  }

  async createOrBindProjectVenv(project_id: string, payload: CreateOrBindVenvRequest): Promise<void> {
    await apiClient.post(`/api/v1/envs/projects/${project_id}/venv`, payload)
  }

  async deleteProjectVenv(project_id: string): Promise<void> {
    await apiClient.delete(`/api/v1/envs/projects/${project_id}/venv`)
  }

  async createSharedVenv(payload: { version: string; shared_venv_key?: string; interpreter_source?: string; python_bin?: string }): Promise<Record<string, unknown>> {
    const res = await apiClient.post<Record<string, unknown>>('/api/v1/envs/venvs', payload)
    return res.data
  }

  async deleteVenv(venv_id: string, allowPrivate = false): Promise<void> {
    await apiClient.delete(`/api/v1/envs/venvs/${venv_id}`, { params: { allow_private: allowPrivate } })
  }

  async updateSharedVenv(venv_id: string, payload: { key?: string }): Promise<void> {
    await apiClient.patch(`/api/v1/envs/venvs/${venv_id}`, payload)
  }

  async installPackagesToVenv(venv_id: string, packages: string[]): Promise<void> {
    await apiClient.post(`/api/v1/envs/venvs/${venv_id}/packages`, { packages })
  }

  async installPackagesToProjectVenv(project_id: string, packages: string[]): Promise<void> {
    await apiClient.post(`/api/v1/envs/projects/${project_id}/venv/packages`, { packages })
  }
}

const envService = new EnvService()
export default envService
