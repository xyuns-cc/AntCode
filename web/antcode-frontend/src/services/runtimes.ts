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

const requireRuntimeScope = (env: RuntimeEnv): RuntimeScope => {
  if (env.scope === 'shared' || env.scope === 'private') {
    return env.scope
  }
  throw new Error(`运行时环境 ${env.name} 缺少 scope`)
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
      scope: requireRuntimeScope(env),
    }))
  }

  async createEnv(workerId: string, payload: { env_name?: string; python_version: string; scope: RuntimeScope; packages?: string[] }, config?: AxiosRequestConfig): Promise<RuntimeEnv> {
    if (payload.scope !== 'shared' && payload.scope !== 'private') {
      throw new Error('创建运行时环境必须提供合法 scope')
    }

    const data = await this.post<{ worker_id: string; env: RuntimeEnv }>(
      `${this.base(workerId)}/runtimes`,
      {
        env_name: payload.env_name,
        python_version: payload.python_version,
        scope: payload.scope,
        packages: payload.packages || [],
      },
      config
    )
    const env = data.env
    return {
      ...env,
      scope: requireRuntimeScope(env),
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

}

export const runtimeService = new RuntimeService()
