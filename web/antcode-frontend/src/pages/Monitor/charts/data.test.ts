import { describe, expect, it } from 'vitest'

import { createTaskStatsData } from './data'

describe('createTaskStatsData', () => {
  it('uses full server aggregates instead of the preview task page', () => {
    const chart = createTaskStatsData({ success: 120, failed: 30, running: 25, pending: 15 })

    expect(chart.datasets[0].data).toEqual([120, 30, 25, 15])
  })
})
