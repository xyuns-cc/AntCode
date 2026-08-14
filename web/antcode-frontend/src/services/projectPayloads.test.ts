import { describe, expect, it } from 'vitest'

import {
  buildRuleConfigPayload,
  createFileConfigFormData,
  createProjectFormData,
} from './projectPayloads'

describe('project payload builders', () => {
  it('preserves rule dispatch constraints including explicit region clearing', () => {
    const form = createProjectFormData({
      name: 'rule-project',
      type: 'rule',
      runtime_scope: 'shared',
      python_version: '3.11',
      region: 'cn-east',
      require_render: false,
    })

    expect(form.get('region')).toBe('cn-east')
    expect(form.get('require_render')).toBe('false')
    expect(buildRuleConfigPayload({ region: undefined, require_render: false })).toEqual({
      region: null,
      require_render: false,
    })
  })

  it('normalizes blank file runtime and environment configuration to objects', () => {
    const form = createFileConfigFormData({
      language: 'typescript',
      entry_point: 'main.py',
      runtime_config: ' ',
      environment_vars: '',
    })

    expect(form.get('language')).toBe('typescript')
    expect(form.get('entry_point')).toBe('main.py')
    expect(form.get('runtime_config')).toBe('{}')
    expect(form.get('environment_vars')).toBe('{}')
  })

  it('serializes file runtime and environment configuration objects', () => {
    const form = createProjectFormData({
      name: 'file-project',
      type: 'file',
      runtime_scope: 'private',
      python_version: '3.12',
      language: 'go',
      runtime_config: { timeout: 30 },
      environment_vars: { MODE: 'test' },
    })

    expect(form.get('language')).toBe('go')
    expect(form.get('runtime_config')).toBe('{"timeout":30}')
    expect(form.get('environment_vars')).toBe('{"MODE":"test"}')
  })

  it('preserves explicit rule disable and normalizes blank JSON fields to empty objects', () => {
    expect(
      buildRuleConfigPayload({
        resume_enabled: false,
        headers: '',
        cookies: ' ',
        proxy_config: '',
        anti_spider: ' ',
        task_config: '',
        data_schema: ' ',
        dedup_config: ' ',
      })
    ).toEqual({
      resume_enabled: false,
      headers: {},
      cookies: {},
      proxy_config: {},
      anti_spider: {},
      task_config: {},
      data_schema: {},
      dedup_config: {},
    })
  })
})
