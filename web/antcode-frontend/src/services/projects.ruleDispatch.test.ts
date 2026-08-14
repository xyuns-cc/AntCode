import { beforeEach, describe, expect, it, vi } from 'vitest'

import apiClient from './api'
import { projectService } from './projects'

vi.mock('./api', () => ({
  default: {
    post: vi.fn(),
    put: vi.fn(),
  },
  unwrapResponse: vi.fn((response: { data: { data: unknown } }) => response.data.data),
}))

const response = { data: { data: { id: 'project-1' } } }

describe('project rule dispatch constraints', () => {
  beforeEach(() => {
    vi.mocked(apiClient.post).mockResolvedValue(response)
    vi.mocked(apiClient.put).mockResolvedValue(response)
  })

  it('persists region and render requirement in rule project FormData', async () => {
    await projectService.createProject({
      name: 'rule-project',
      type: 'rule',
      runtime_scope: 'shared',
      python_version: '3.11',
      target_url: 'https://example.com',
      extraction_rules: '[{"desc":"title","type":"css","expr":"h1"}]',
      region: 'cn-east',
      require_render: true,
    })

    const form = vi.mocked(apiClient.post).mock.calls[0][1] as FormData
    expect(form.get('region')).toBe('cn-east')
    expect(form.get('require_render')).toBe('true')
  })

  it('allows rule updates to clear region and disable explicit render requirement', async () => {
    await projectService.updateRuleConfig('project-1', {
      region: undefined,
      require_render: false,
    })

    const payload = vi.mocked(apiClient.put).mock.calls[0][1]
    expect(payload).toEqual({ region: null, require_render: false })
  })
})
