import { describe, expect, it } from 'vitest'
import { isTerminalTaskStatus } from './taskStatus'

describe('isTerminalTaskStatus', () => {
  it.each(['success', 'failed', 'cancelled', 'timeout', 'rejected', 'skipped'])(
    'stops polling for %s',
    (status) => expect(isTerminalTaskStatus(status)).toBe(true),
  )

  it.each(['pending', 'dispatching', 'queued', 'running', 'paused'])(
    'keeps polling for %s',
    (status) => expect(isTerminalTaskStatus(status)).toBe(false),
  )
})
