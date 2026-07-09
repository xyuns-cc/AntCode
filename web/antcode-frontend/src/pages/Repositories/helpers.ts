import type { ProjectImportItem, RepositoryCandidate, RepositoryScanResult, GitRepository } from '@/types/repository'

export const buildImportDefaults = (
  result: RepositoryScanResult,
  repository: GitRepository,
  selected: string[],
) => {
  const projects: Record<string, ProjectImportItem> = {}
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
      runtime_scope: 'shared',
      runtime_kind: 'python',
      execution_strategy: 'auto',
    }
  })
  return { projects }
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
