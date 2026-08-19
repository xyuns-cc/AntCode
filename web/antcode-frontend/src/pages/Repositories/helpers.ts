import type {
  GitRepository,
  ProjectImportEditable,
  ProjectImportFormValues,
  ProjectImportItem,
  RepositoryCandidate,
  RepositoryCreatePayload,
  RepositoryScanResult,
  RepositoryUpdatePayload,
} from '@/types/repository'

const PYTHON_VERSION_PATTERN = /^[0-9]+\.[0-9]+(?:\.[0-9]+)?$/

export interface RepositoryFormValues {
  name: string
  url: string
  default_ref: string
  credential_id?: string
}

export const buildRepositoryCreatePayload = (
  values: RepositoryFormValues,
): RepositoryCreatePayload => ({
  name: values.name.trim(),
  url: values.url.trim(),
  default_ref: values.default_ref.trim(),
  credential_id: values.credential_id,
})

export const buildRepositoryUpdatePayload = (
  values: RepositoryFormValues,
): RepositoryUpdatePayload => ({
  ...buildRepositoryCreatePayload(values),
  // 清空 antd Select 拿到的是 undefined，而 JSON.stringify 会整条丢掉 undefined
  // 字段，后端只能看到"没传 credential_id"→ 保持原值，解绑意图被静默吃掉。
  credential_id: values.credential_id ?? null,
})

// 表单里只注册了 name / include_paths 两个 Form.Item（见 ScanImportDrawer
// 的 buildCandidateColumns），因此 antd 的 validateFields() 只会回传这两项。
// 其余字段（repository_id / ref / subdir / entry_point 等）不是用户可编辑的，
// 必须由扫描结果重建 —— 之前塞进表单 store 再指望 validateFields() 带回来，
// 结果被 antd 原样丢弃，请求缺 repository_id 被后端 422 挡下。
export const buildImportDefaults = (
  result: RepositoryScanResult,
  selected: string[],
): Pick<ProjectImportFormValues, 'projects'> => {
  const projects: Record<string, ProjectImportEditable> = {}
  result.candidates.forEach(candidate => {
    if (!selected.includes(candidate.subdir)) return
    projects[candidate.subdir] = {
      include_paths: [],
      name: candidate.subdir.split('/').join('-'),
    }
  })
  return { projects }
}

export const buildImportProjects = (
  values: ProjectImportFormValues,
  selected: string[],
  result: RepositoryScanResult,
  repository: GitRepository,
): ProjectImportItem[] => {
  const workerId = values.worker_id?.trim()
  const pythonVersion = values.python_version?.trim()
  if (!workerId) throw new Error('仓库导入必须选择 Worker')
  if (!pythonVersion || !PYTHON_VERSION_PATTERN.test(pythonVersion)) {
    throw new Error('仓库导入必须指定有效的 Python 版本')
  }
  return selected.map(subdir => {
    const candidate = result.candidates.find(item => item.subdir === subdir)
    if (!candidate) throw new Error(`扫描结果中缺少子目录: ${subdir}`)
    const project = values.projects?.[subdir]
    if (!project) throw new Error(`缺少项目导入配置: ${subdir}`)
    const name = project.name?.trim()
    if (!name) throw new Error(`缺少项目名称: ${subdir}`)
    return {
      repository_id: repository.id,
      ref: result.ref,
      subdir: candidate.subdir,
      entry_point: candidate.entry_point,
      include_paths: project.include_paths ?? [],
      name,
      description: '',
      tags: [],
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
