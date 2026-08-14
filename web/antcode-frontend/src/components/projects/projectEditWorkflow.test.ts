import { beforeEach, describe, expect, it, vi } from 'vitest'

import { projectService } from '@/services/projects'
import type { Project } from '@/types'
import { buildProjectUpdateSteps, getProjectInitialData } from './projectEditWorkflow'

vi.mock('@/services/projects', () => ({
  projectService: {
    updateFileConfig: vi.fn(),
    updateProject: vi.fn(),
    updateProjectSource: vi.fn(),
  },
}))

const baseProject = {
  id: 'project-1',
  name: 'project',
  type: 'file',
  status: 'active',
  created_at: '2026-08-12T00:00:00Z',
  updated_at: '2026-08-12T00:00:00Z',
  created_by: 'user-1',
} as const

describe('project edit workflow', () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  it('keeps FILE language in initial data and update payload', async () => {
    const project: Project = {
      ...baseProject,
      file_info: { language: 'typescript', entry_point: 'src/main.ts' },
    }

    expect(getProjectInitialData(project)).toMatchObject({ language: 'typescript' })

    const steps = buildProjectUpdateSteps(
      project,
      { repository_id: 'repo-1', language: 'go', entry_point: 'main.go' },
      {}
    )
    await steps.at(-1)?.run()

    expect(projectService.updateFileConfig).toHaveBeenCalledWith('project-1', {
      language: 'go',
      entry_point: 'main.go',
      runtime_config: undefined,
      environment_vars: undefined,
    })
  })

  it('keeps rule resume and dedup configuration in initial data', () => {
    const project: Project = {
      ...baseProject,
      type: 'rule',
      rule_info: {
        engine: 'requests',
        require_render: false,
        target_url: 'https://example.com',
        callback_type: 'list',
        request_method: 'GET',
        max_pages: 1,
        start_page: 1,
        request_delay: 1000,
        retry_count: 3,
        timeout: 30,
        resume_enabled: false,
        dedup_config: { enabled: false, fields: ['url'] },
      },
    }

    expect(getProjectInitialData(project)).toMatchObject({
      resume_enabled: false,
      dedup_config: { enabled: false, fields: ['url'] },
    })
  })
})
