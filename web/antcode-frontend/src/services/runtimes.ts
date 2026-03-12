import type { AxiosRequestConfig } from 'axios'
import { BaseService } from './base'

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

class RuntimeService extends BaseService {
  constructor() {
    super('/api/v1/runtimes')
  }

  private base(workerId: string) {
    return `/workers/${workerId}`
  }

  async listEnvs(workerId: string, scope?: RuntimeScope, config?: AxiosRequestConfig): Promise<RuntimeEnv[]> {
    const items = await this.get<RuntimeEnv[]>(
      `${this.base(workerId)}/runtimes`,
      {
        ...config,
        params: { ...(scope ? { scope } : {}), ...(config?.params ?? {}) }
      }
    )
    return items.map((env) => ({
      ...env,
      scope: env.scope || (env.name?.startsWith('shared-') ? 'shared' : 'private'),
    }))
  }

  async createEnv(workerId: string, payload: { env_name?: string; python_version: string; scope: RuntimeScope; packages?: string[] }, config?: AxiosRequestConfig): Promise<RuntimeEnv> {
    const normalizedScope = payload.scope === 'shared' || payload.scope === 'private'
      ? payload.scope
      : (payload.env_name?.startsWith('shared-') ? 'shared' : 'private')

    const data = await this.post<{ worker_id: string; env: RuntimeEnv }>(
      `${this.base(workerId)}/runtimes`,
      {
        env_name: payload.env_name,
        python_version: payload.python_version,
        scope: normalizedScope,
        packages: payload.packages || [],
      },
      config
    )
    const env = data.env
    return {
      ...env,
      scope: env.scope || (env.name?.startsWith('shared-') ? 'shared' : 'private'),
    }
  }

  async updateEnv(workerId: string, envName: string, payload: { key?: string; description?: string }, config?: AxiosRequestConfig): Promise<RuntimeEnv> {
    return await this.patch<RuntimeEnv>(
      `${this.base(workerId)}/runtimes/${encodeURIComponent(envName)}`,
      payload,
      config
    )
  }

  async deleteEnv(workerId: string, envName: string, config?: AxiosRequestConfig): Promise<void> {
    await this.delete(`${this.base(workerId)}/runtimes/${encodeURIComponent(envName)}`, config)
  }

  async listPackages(workerId: string, envName: string, config?: AxiosRequestConfig): Promise<RuntimePackage[]> {
    return await this.get<RuntimePackage[]>(
      `${this.base(workerId)}/runtimes/${encodeURIComponent(envName)}/packages`,
      config
    )
  }

  async installPackages(workerId: string, envName: string, packages: string[], upgrade?: boolean, config?: AxiosRequestConfig): Promise<void> {
    await this.post(
      `${this.base(workerId)}/runtimes/${encodeURIComponent(envName)}/packages`,
      { packages, upgrade: !!upgrade },
      config
    )
  }

  async uninstallPackages(workerId: string, envName: string, packages: string[], config?: AxiosRequestConfig): Promise<void> {
    await this.delete(
      `${this.base(workerId)}/runtimes/${encodeURIComponent(envName)}/packages`,
      { ...config, data: { packages, upgrade: false } }
    )
  }

  async listInterpreters(workerId: string, config?: AxiosRequestConfig): Promise<RuntimeInterpreter[]> {
    return await this.get<RuntimeInterpreter[]>(
      `${this.base(workerId)}/interpreters`,
      config
    )
  }

  async installInterpreter(workerId: string, version: string, config?: AxiosRequestConfig): Promise<void> {
    await this.post(`${this.base(workerId)}/interpreters`, { version }, config)
  }

  async registerInterpreter(workerId: string, python_bin: string, version?: string, config?: AxiosRequestConfig): Promise<void> {
    await this.post(`${this.base(workerId)}/interpreters/register`, { python_bin, version }, config)
  }

  async removeInterpreter(workerId: string, payload: { version?: string; python_bin?: string; mode?: 'uninstall' | 'unregister' }, config?: AxiosRequestConfig): Promise<void> {
    if (payload.mode === 'unregister' && !payload.version && !payload.python_bin) {
      throw new Error('移除本地解释器需要提供 version 或 python_bin')
    }
    if (payload.mode !== 'unregister' && !payload.version) {
      throw new Error('卸载解释器需要提供 version')
    }

    await this.delete(`${this.base(workerId)}/interpreters`, {
      ...config,
      params: { ...(payload ?? {}), ...(config?.params ?? {}) }
    })
  }

  async getPythonVersions(workerId: string, config?: AxiosRequestConfig): Promise<RuntimePythonVersions> {
    return await this.get<RuntimePythonVersions>(
      `${this.base(workerId)}/python-versions`,
      config
    )
  }

  async installPythonVersion(workerId: string, version: string, config?: AxiosRequestConfig): Promise<void> {
    await this.post(`${this.base(workerId)}/python-versions/${encodeURIComponent(version)}/install`, undefined, config)
  }
}

export const runtimeService = new RuntimeService()
