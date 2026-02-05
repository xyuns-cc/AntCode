import { globalNotification, type NoticeType, showNotification as showNotificationFromHook } from '@/hooks/useMessage'

export type { NoticeType }

export function showNotification(
  type: NoticeType,
  message: string,
  description?: string,
  options?: { placement?: string; duration?: number; key?: string; durationMs?: number; meta?: Record<string, unknown> }
) {
  return showNotificationFromHook(type, message, description, options)
}

export const notification = {
  success: (message: string, description?: string, duration?: number) => globalNotification.success(message, description, duration),
  error: (message: string, description?: string, duration?: number) => globalNotification.error(message, description, duration),
  warning: (message: string, description?: string, duration?: number) => globalNotification.warning(message, description, duration),
  info: (message: string, description?: string, duration?: number) => globalNotification.info(message, description, duration),
}

export default showNotification
