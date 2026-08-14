import { describe, expect, it } from 'vitest'
import {
  RUNTIME_DESCRIPTION_MAX_BYTES,
  RUNTIME_KEY_MAX_BYTES,
  validateRuntimeMetadata,
} from './runtimes'

describe('validateRuntimeMetadata', () => {
  it('accepts UTF-8 metadata at the shared byte boundary', () => {
    expect(() => validateRuntimeMetadata({
      key: 'a'.repeat(RUNTIME_KEY_MAX_BYTES),
      description: 'a'.repeat(RUNTIME_DESCRIPTION_MAX_BYTES),
    })).not.toThrow()
  })

  it('rejects multibyte metadata over the shared byte boundary', () => {
    const description = '界'.repeat(Math.floor(RUNTIME_DESCRIPTION_MAX_BYTES / 3) + 1)

    expect(() => validateRuntimeMetadata({ description })).toThrow('环境描述 UTF-8')
  })
})
