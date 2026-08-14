import type { RuntimeEnv } from '@/services/runtimes'
import type { Worker } from '@/types'
import type { ExtendedRuntimeEnvItem } from './types'

export const buildWorkerEnvId = (workerId: string, envName: string) => `${workerId}|${envName}`

export const toWorkerRuntimeEnvItem = (worker: Worker, env: RuntimeEnv): ExtendedRuntimeEnvItem => {
  if (env.scope !== 'shared' && env.scope !== 'private') {
    throw new Error(`运行时环境 ${env.name} 缺少 scope`)
  }
  return {
    id: buildWorkerEnvId(worker.id, env.name),
    scope: env.scope,
    key: env.key || env.name,
    description: env.description || null,
    version: env.python_version,
    runtime_locator: env.path,
    created_by_username: env.created_by || null,
    created_at: env.created_at || null,
    updated_at: null,
    current_project_id: null,
    packages: undefined,
    workerName: worker.name,
    workerId: worker.id,
    envName: env.name,
  }
}
