import { globalModal } from '@/hooks/useMessage'
import { projectService } from '@/services/projects'
import type {
  Project,
  ProjectCodeConfigUpdateRequest,
  ProjectFileConfigUpdateRequest,
  ProjectSourcePayload,
  ProjectUpdateRequest,
} from '@/types'
import showNotification from '@/utils/notification'
import type { RuleDispatchConfig } from './ProjectCreateDrawerContent'

interface ProjectUpdateStep {
  name: string
  run: () => Promise<void>
}

const normalizeSourceList = (value: unknown): string[] => {
  if (Array.isArray(value)) {
    return value.filter((item): item is string => typeof item === 'string')
  }
  if (typeof value !== 'string') return []
  return value
    .split(',')
    .map((item) => item.trim())
    .filter(Boolean)
}

const buildSourcePayload = (data: Record<string, unknown>): ProjectSourcePayload => ({
  repository_id: String(data.repository_id ?? ''),
  ref: String(data.ref ?? 'main'),
  subdir: String(data.subdir ?? ''),
  include_paths: normalizeSourceList(data.include_paths),
})

const normalizeTags = (value: unknown): string[] | undefined => {
  if (Array.isArray(value)) {
    return value.filter((tag): tag is string => typeof tag === 'string')
  }
  if (typeof value !== 'string') return undefined
  return value
    .split(',')
    .map((tag) => tag.trim())
    .filter(Boolean)
}

const buildBaseUpdate = (data: Record<string, unknown>): ProjectUpdateRequest => {
  const update: ProjectUpdateRequest = {}
  if (typeof data.name === 'string') update.name = data.name
  if (typeof data.description === 'string') update.description = data.description
  const tags = normalizeTags(data.tags)
  if (tags) update.tags = tags
  if (Array.isArray(data.dependencies)) update.dependencies = data.dependencies as string[]
  return update
}

const buildRuleStep = (
  project: Project,
  data: Record<string, unknown>,
  config: RuleDispatchConfig
): ProjectUpdateStep => ({
  name: '规则配置',
  run: async () => {
    await projectService.updateRuleConfig(project.id, {
      ...(data as Partial<ProjectUpdateRequest>),
      region: config.region,
      require_render: data.engine === 'playwright' || Boolean(config.require_render),
    })
  },
})

const buildTypedConfigurationStep = (
  project: Project,
  data: Record<string, unknown>
): ProjectUpdateStep | null => {
  if (project.type === 'code') {
    const payload: ProjectCodeConfigUpdateRequest = {
      language: data.language as string | undefined,
      entry_point: data.code_entry_point as string | undefined,
      documentation: data.documentation as string | undefined,
    }
    return {
      name: '代码配置',
      run: async () => {
        await projectService.updateCodeConfig(project.id, payload)
      },
    }
  }
  if (project.type !== 'file') return null
  const payload: ProjectFileConfigUpdateRequest = {
    language: data.language as string | undefined,
    entry_point: data.entry_point as string | undefined,
    runtime_config: data.runtime_config as ProjectFileConfigUpdateRequest['runtime_config'],
    environment_vars: data.environment_vars as ProjectFileConfigUpdateRequest['environment_vars'],
  }
  return {
    name: '文件配置',
    run: async () => {
      await projectService.updateFileConfig(project.id, payload)
    },
  }
}

export const buildProjectUpdateSteps = (
  project: Project,
  data: Record<string, unknown>,
  config: RuleDispatchConfig
): ProjectUpdateStep[] => {
  const steps: ProjectUpdateStep[] = []
  const baseUpdate = buildBaseUpdate(data)
  if (Object.keys(baseUpdate).length > 0) {
    steps.push({
      name: '项目基本信息',
      run: async () => {
        await projectService.updateProject(project.id, baseUpdate)
      },
    })
  }
  if (project.type === 'rule') return [...steps, buildRuleStep(project, data, config)]
  steps.push({
    name: 'Git 来源',
    run: async () => {
      await projectService.updateProjectSource(project.id, buildSourcePayload(data))
    },
  })
  const typedStep = buildTypedConfigurationStep(project, data)
  if (typedStep) steps.push(typedStep)
  return steps
}

const confirmContinue = (step: ProjectUpdateStep, error: unknown): Promise<boolean> => {
  const message = error instanceof Error ? error.message : '未知错误'
  return new Promise((resolve) => {
    globalModal.confirm({
      title: `${step.name}保存失败`,
      content: `${step.name}保存失败：${message}。是否继续后续步骤？`,
      okText: '继续',
      cancelText: '中止',
      onOk: () => resolve(true),
      onCancel: () => resolve(false),
    })
  })
}

export const executeProjectUpdateSteps = async (steps: ProjectUpdateStep[]): Promise<string[]> => {
  const failures: string[] = []
  for (const [index, step] of steps.entries()) {
    try {
      await step.run()
    } catch (error) {
      failures.push(step.name)
      if (index === steps.length - 1) {
        showNotification('error', `${step.name}保存失败`, (error as Error).message)
        break
      }
      if (!(await confirmContinue(step, error))) break
    }
  }
  return failures
}

const serializeValue = (value: unknown, fallback: string, pretty = false): string => {
  if (!value) return fallback
  if (typeof value === 'string') return value
  return JSON.stringify(value, null, pretty ? 2 : undefined)
}

const buildRuleInitialData = (project: Project) => {
  const info = project.rule_info!
  return {
    engine: info.engine,
    region: info.region,
    require_render: info.require_render,
    target_url: info.target_url,
    url_pattern: info.url_pattern,
    request_delay: info.request_delay / 1000,
    request_method: info.request_method,
    callback_type: info.callback_type,
    max_pages: info.max_pages,
    start_page: info.start_page,
    priority: info.priority,
    retry_count: info.retry_count,
    timeout: info.timeout,
    dont_filter: info.dont_filter,
    extraction_rules: serializeValue(info.extraction_rules, '[]'),
    pagination_config: serializeValue(
      info.pagination_config,
      JSON.stringify({ method: 'none', max_pages: 10, start_page: 1 })
    ),
    headers: serializeValue(info.headers, '', true),
    cookies: serializeValue(info.cookies, '', true),
    proxy_config: info.proxy_config,
    anti_spider: info.anti_spider,
    task_config: info.task_config,
    data_schema: info.data_schema,
    resume_enabled: info.resume_enabled,
    dedup_config: info.dedup_config,
  }
}

export const getProjectInitialData = (project: Project) => {
  const baseData = {
    name: project.name,
    description: project.description,
    tags: Array.isArray(project.tags) ? project.tags.join(', ') : project.tags,
  }
  if (project.type === 'rule' && project.rule_info) {
    return { ...baseData, ...buildRuleInitialData(project) }
  }
  if (project.type === 'code' && project.code_info) {
    return {
      ...baseData,
      repository_id: project.code_info.repository_id,
      ref: project.code_info.ref,
      subdir: project.code_info.subdir,
      include_paths: project.code_info.include_paths,
      language: project.code_info.language,
      code_entry_point: project.code_info.entry_point,
      documentation: project.code_info.documentation,
      dependencies: project.dependencies || [],
    }
  }
  if (project.type === 'file' && project.file_info) {
    return {
      ...baseData,
      repository_id: project.file_info.repository_id,
      ref: project.file_info.ref,
      subdir: project.file_info.subdir,
      include_paths: project.file_info.include_paths,
      language: project.file_info.language,
      entry_point: project.file_info.entry_point,
      runtime_config: serializeValue(project.file_info.runtime_config, '', true),
      environment_vars: serializeValue(project.file_info.environment_vars, '', true),
      dependencies: project.dependencies || [],
      file_info: project.file_info,
    }
  }
  return baseData
}
