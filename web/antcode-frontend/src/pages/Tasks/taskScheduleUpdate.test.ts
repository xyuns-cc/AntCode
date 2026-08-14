import { describe, expect, it } from 'vitest'

import { buildTaskScheduleUpdate, TASK_SCHEDULE_OPTIONS } from './taskScheduleUpdate'

const SCHEDULED_TIME = '2026-08-12T03:04:05.000Z'
const scheduleValues = {
  cron_expression: '0 1 * * *',
  interval_seconds: 300,
  scheduled_time: { toISOString: () => SCHEDULED_TIME },
}

describe('task schedule update contract', () => {
  it('exposes every backend schedule type in the edit options', () => {
    expect(TASK_SCHEDULE_OPTIONS.map(({ value }) => value)).toEqual([
      'once',
      'date',
      'interval',
      'cron',
    ])
  })

  it('sends only the cron field for cron schedules', () => {
    expect(buildTaskScheduleUpdate({ ...scheduleValues, schedule_type: 'cron' })).toEqual({
      schedule_type: 'cron',
      cron_expression: scheduleValues.cron_expression,
    })
  })

  it('sends only the interval field for interval schedules', () => {
    expect(buildTaskScheduleUpdate({ ...scheduleValues, schedule_type: 'interval' })).toEqual({
      schedule_type: 'interval',
      interval_seconds: scheduleValues.interval_seconds,
    })
  })

  it.each(['date', 'once'] as const)(
    'sends only scheduled_time for %s schedules',
    (scheduleType) => {
      expect(buildTaskScheduleUpdate({ ...scheduleValues, schedule_type: scheduleType })).toEqual({
        schedule_type: scheduleType,
        scheduled_time: SCHEDULED_TIME,
      })
    }
  )
})
