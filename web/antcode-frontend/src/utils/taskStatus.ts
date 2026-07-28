import type { TaskStatus } from '@/types'

export const TERMINAL_TASK_STATUSES: ReadonlySet<TaskStatus> = new Set([
  'success',
  'failed',
  'cancelled',
  'timeout',
  'rejected',
  'skipped',
])

export const isTerminalTaskStatus = (status: string | null | undefined): boolean => {
  return TERMINAL_TASK_STATUSES.has(status as TaskStatus)
}
