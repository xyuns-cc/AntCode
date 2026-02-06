import type { AxiosRequestConfig } from 'axios'
import apiClient from './api'

export type RuntimeScope = 'private' | 'shared'

export interface RuntimeEnv {
  name: string
  path: string
  python_version: string
  python_executable: string
  created_at?: string
  created_by?: string
  packages_count?: number
  scope?: RuntimeScope
}

export interface RuntimePackage {
  name: string
  version: string
}

export interface RuntimeInterpreter {
  version: string
  source?: string
  python_bin?: string
  install_dir?: string
  is_available?: boolean
  created_at?: string
  updated_at?: string
}

export interface RuntimePythonVersions {
  installed?: RuntimeInterpreter[]
  available?: string[]
  all_interpreters?: RuntimeInterpreter[]
  platform?: {
    os_type: string
    os_version: string
    python_version: string
    machine: string
    mise_available: boolean
  }
}

class RuntimeService {
  private base(workerId: string) {
    return `/api/v1/runtimes/workers/${workerId}`
  }

  async listEnvs(workerId: string, scope?: RuntimeScope, config?: AxiosRequestConfig): Promise<RuntimeEnv[]> {
    const res = await apiClient.get<{ data: RuntimeEnv[] | { envs?: RuntimeEnv[] } }>(
      `${this.base(workerId)}/envs`,
      {
        ...config,
        params: { ...(scope ? { scope } : {}), ...(config?.params ?? {}) }
      }
    )
    const data = res.data.data
    const items = Array.isArray(data) ? data : data?.envs || []
    return items.map((env) => ({
      ...env,
      scope: env.scope || (env.name?.startsWith('shared-') ? 'shared' : 'private'),
    }))
  }

  async createEnv(workerId: string, payload: { env_name?: string; name?: string; python_version: string; scope?: RuntimeScope | string; packages?: string[] }, config?: AxiosRequestConfig): Promise<RuntimeEnv> {
    const res = await apiClient.post<{ data: { env: RuntimeEnv } | RuntimeEnv }>(
      `${this.base(workerId)}/envs`,
      payload,
      config
    )
    const data = res.data.data
    const env = (data && typeof data === 'object' && 'env' in data) ? (data as { env: RuntimeEnv }).env : (data as RuntimeEnv)
    return {
      ...env,
      scope: env.scope || (env.name?.startsWith('shared-') ? 'shared' : 'private'),
    }
  }

  async updateEnv(workerId: string, envName: string, payload: { key?: string; description?: string }, config?: AxiosRequestConfig): Promise<RuntimeEnv> {
    const res = await apiClient.patch<{ data: RuntimeEnv }>(
      `${this.base(workerId)}/envs/${encodeURIComponent(envName)}`,
      payload,
      config
    )
    return res.data.data
  }

  async deleteEnv(workerId: string, envName: string, config?: AxiosRequestConfig): Promise<void> {
    await apiClient.delete(`${this.base(workerId)}/envs/${encodeURIComponent(envName)}`, config)
  }

  async listPackages(workerId: string, envName: string, config?: AxiosRequestConfig): Promise<RuntimePackage[]> {
    const res = await apiClient.get<{ data: RuntimePackage[] }>(
      `${this.base(workerId)}/envs/${encodeURIComponent(envName)}/packages`,
      config
    )
    return res.data.data || []
  }

  async installPackages(workerId: string, envName: string, packages: string[], upgrade?: boolean, config?: AxiosRequestConfig): Promise<void> {
    await apiClient.post(
      `${this.base(workerId)}/envs/${encodeURIComponent(envName)}/packages`,
      { packages, upgrade: !!upgrade },
      config
    )
  }

  async uninstallPackages(workerId: string, envName: string, packages: string[], config?: AxiosRequestConfig): Promise<void> {
    await apiClient.delete(
      `${this.base(workerId)}/envs/${encodeURIComponent(envName)}/packages`,
      { ...config, data: { packages, upgrade: false } }
    )
  }

  async listInterpreters(workerId: string, config?: AxiosRequestConfig): Promise<RuntimeInterpreter[]> {
    const res = await apiClient.get<{ data: RuntimeInterpreter[] }>(
      `${this.base(workerId)}/interpreters`,
      config
    )
    return res.data.data || []
  }

  async installInterpreter(workerId: string, version: string, config?: AxiosRequestConfig): Promise<void> {
    await apiClient.post(`${this.base(workerId)}/interpreters`, { version }, config)
  }

  async registerInterpreter(workerId: string, python_bin: string, version?: string, config?: AxiosRequestConfig): Promise<void> {
    await apiClient.post(`${this.base(workerId)}/interpreters/register`, { python_bin, version }, config)
  }

  async removeInterpreter(workerId: string, payload: { version?: string; python_bin?: string; mode?: 'uninstall' | 'unregister' }, config?: AxiosRequestConfig): Promise<void> {
    await apiClient.delete(`${this.base(workerId)}/interpreters`, {
      ...config,
      params: { ...(payload ?? {}), ...(config?.params ?? {}) }
    })
  }

  async getPythonVersions(workerId: string, config?: AxiosRequestConfig): Promise<RuntimePythonVersions> {
    const res = await apiClient.get<{ data: RuntimePythonVersions }>(
      `${this.base(workerId)}/python-versions`,
      config
    )
    return res.data.data || {}
  }

  async installPythonVersion(workerId: string, version: string, config?: AxiosRequestConfig): Promise<void> {
    await apiClient.post(`${this.base(workerId)}/python-versions/${encodeURIComponent(version)}/install`, undefined, config)
  }
}

export const runtimeService = new RuntimeService()
