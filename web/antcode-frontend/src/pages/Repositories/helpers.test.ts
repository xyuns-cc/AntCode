import { describe, expect, it } from 'vitest'
import type { GitRepository, RepositoryScanResult } from '@/types/repository'
import { buildImportDefaults, buildImportProjects } from './helpers'

const repository: GitRepository = {
  id: 'repo-1',
  name: 'crawler',
  url: 'https://example.test/crawler.git',
  default_ref: 'main',
  enabled: true,
  created_at: '2026-07-27T00:00:00Z',
  updated_at: '2026-07-27T00:00:00Z',
}

const scanResult: RepositoryScanResult = {
  repository_id: repository.id,
  ref: 'main',
  candidates: [{ subdir: 'spiders/news', entry_point: 'main.py', markers: ['main.py'] }],
}

describe('repository import helpers', () => {
  it('binds every imported project to the selected runtime Worker', () => {
    const defaults = buildImportDefaults(scanResult, repository, ['spiders/news'])
    const projects = buildImportProjects({
      ...defaults,
      worker_id: 'worker-1',
      python_version: '3.11.9',
    }, ['spiders/news'])

    expect(projects[0]).toMatchObject({
      worker_id: 'worker-1',
      bound_worker_id: 'worker-1',
      python_version: '3.11.9',
      runtime_scope: 'private',
      execution_strategy: 'fixed',
    })
  })

  it.each([
    [{ worker_id: undefined, python_version: '3.11' }, '必须选择 Worker'],
    [{ worker_id: 'worker-1', python_version: 'latest' }, '有效的 Python 版本'],
  ])('rejects an incomplete runtime selection', (runtime, message) => {
    const defaults = buildImportDefaults(scanResult, repository, ['spiders/news'])

    expect(() => buildImportProjects({ ...defaults, ...runtime }, ['spiders/news'])).toThrow(message)
  })
})
