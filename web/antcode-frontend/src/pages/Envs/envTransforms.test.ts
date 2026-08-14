import { describe, expect, it } from 'vitest'
import { toWorkerRuntimeEnvItem } from './envTransforms'

describe('toWorkerRuntimeEnvItem', () => {
  it('preserves editable Worker runtime metadata', () => {
    const item = toWorkerRuntimeEnvItem(
      { id: 'worker-1', name: 'Worker 1' } as never,
      {
        name: 'shared-py311',
        path: '/runtime/shared-py311',
        python_version: '3.11.11',
        python_executable: '/runtime/shared-py311/bin/python',
        scope: 'shared',
        key: 'analytics',
        description: 'Analytics dependencies',
      },
    )

    expect(item.key).toBe('analytics')
    expect(item.description).toBe('Analytics dependencies')
    expect(item.scope).toBe('shared')
  })
})
