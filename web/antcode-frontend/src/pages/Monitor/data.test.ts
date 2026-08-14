import { describe, expect, it } from 'vitest'

import * as monitorData from './data'

describe('Monitor data contracts', () => {
  it('does not present derived Worker state as authoritative logs', () => {
    expect(monitorData).not.toHaveProperty('createWorkerLogs')
  })
})
