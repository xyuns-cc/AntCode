import type { ScheduleType, TaskUpdateRequest } from '@/types'

export interface TaskScheduleFormValues {
  schedule_type: ScheduleType
  cron_expression?: string
  interval_seconds?: number
  scheduled_time?: { toISOString: () => string } | null
}

type TaskScheduleUpdate = Pick<TaskUpdateRequest, 'schedule_type'> &
  Partial<Pick<TaskUpdateRequest, 'cron_expression' | 'interval_seconds' | 'scheduled_time'>>

export const TASK_SCHEDULE_OPTIONS: ReadonlyArray<{ label: string; value: ScheduleType }> = [
  { label: '一次性', value: 'once' },
  { label: '指定时间执行', value: 'date' },
  { label: '间隔执行', value: 'interval' },
  { label: 'Cron表达式', value: 'cron' },
]

export function buildTaskScheduleUpdate(values: TaskScheduleFormValues): TaskScheduleUpdate {
  switch (values.schedule_type) {
    case 'cron':
      return {
        schedule_type: values.schedule_type,
        ...(values.cron_expression === undefined
          ? {}
          : { cron_expression: values.cron_expression }),
      }
    case 'interval':
      return {
        schedule_type: values.schedule_type,
        ...(values.interval_seconds === undefined
          ? {}
          : { interval_seconds: values.interval_seconds }),
      }
    case 'date':
    case 'once': {
      const scheduledTime = values.scheduled_time?.toISOString()
      return {
        schedule_type: values.schedule_type,
        ...(scheduledTime === undefined ? {} : { scheduled_time: scheduledTime }),
      }
    }
  }
}
