import type { EnvironmentConfig } from '@/components/runtimes/EnvSelector'
import type { ProjectCreateRequest } from '@/types'
import type { RuleDispatchConfig } from './ProjectCreateDrawerContent'

export const buildRuleProjectRequest = (
  data: ProjectCreateRequest,
  config: RuleDispatchConfig
): ProjectCreateRequest => ({
  ...data,
  region: config.region,
  require_render: data.engine === 'playwright' || Boolean(config.require_render),
  runtime_scope: 'shared',
  python_version: '3.11',
})

export const validateEnvironmentConfig = (config: EnvironmentConfig | null): string | null => {
  if (!config) return '请配置运行环境'
  if (config.location === 'worker' && !config.workerId) return '使用 Worker 环境时必须选择 Worker'
  if (config.useExisting && !config.existingEnvName) return '请选择要使用的环境'
  if (!config.useExisting && !config.pythonVersion) return '创建新环境时必须指定Python版本'
  return null
}

export const buildEnvironmentProjectRequest = (
  data: ProjectCreateRequest,
  config: EnvironmentConfig
): ProjectCreateRequest => ({
  ...data,
  env_location: config.location,
  worker_id: config.workerId,
  runtime_scope: config.scope,
  use_existing_env: config.useExisting,
  existing_env_name: config.existingEnvName,
  python_version: config.pythonVersion ?? data.python_version ?? '3.11',
  env_name: config.envName,
  env_description: config.envDescription,
})
