import type {
  GitRepository,
  ProjectImportDraft,
  ProjectImportFormValues,
  ProjectImportItem,
  RepositoryCandidate,
  RepositoryScanResult,
} from '@/types/repository'

const PYTHON_VERSION_PATTERN = /^[0-9]+\.[0-9]+(?:\.[0-9]+)?$/

export const buildImportDefaults = (
  result: RepositoryScanResult,
  repository: GitRepository,
  selected: string[],
) => {
  const projects: Record<string, ProjectImportDraft> = {}
  result.candidates.forEach(candidate => {
    if (!selected.includes(candidate.subdir)) return
    projects[candidate.subdir] = {
      repository_id: repository.id,
      ref: result.ref,
      subdir: candidate.subdir,
      entry_point: candidate.entry_point,
      include_paths: [],
      name: candidate.subdir.split('/').join('-'),
      description: '',
      tags: [],
      runtime_scope: 'private',
      runtime_kind: 'python',
      execution_strategy: 'fixed',
    }
  })
  return { projects }
}

export const buildImportProjects = (
  values: ProjectImportFormValues,
  selected: string[],
): ProjectImportItem[] => {
  const workerId = values.worker_id?.trim()
  const pythonVersion = values.python_version?.trim()
  if (!workerId) throw new Error('仓库导入必须选择 Worker')
  if (!pythonVersion || !PYTHON_VERSION_PATTERN.test(pythonVersion)) {
    throw new Error('仓库导入必须指定有效的 Python 版本')
  }
  return selected.map(subdir => {
    const project = values.projects[subdir]
    if (!project) throw new Error(`缺少项目导入配置: ${subdir}`)
    return {
      ...project,
      runtime_scope: 'private',
      runtime_kind: 'python',
      python_version: pythonVersion,
      worker_id: workerId,
      execution_strategy: 'fixed',
      bound_worker_id: workerId,
    }
  })
}

export const sharedPathOptions = (
  candidates: RepositoryCandidate[],
  current: RepositoryCandidate,
  repository: GitRepository | null,
) => {
  if (!repository) return []
  const topDirs = new Set(
    candidates
      .map(item => item.subdir.split('/')[0])
      .filter(item => item && item !== current.subdir.split('/')[0])
  )
  return Array.from(topDirs).map(value => ({ label: value, value }))
}
