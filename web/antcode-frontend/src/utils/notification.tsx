import { globalNotification, type NoticeType, showNotification as showNotificationFromHook, type NotificationPlacement } from '@/hooks/useMessage'
import type { ReactNode } from 'react'

export type { NoticeType }

const splitTitleAndDetail = (text: string): { title: string; detail?: string } => {
  const trimmed = text.trim()
  if (!trimmed) {
    return { title: '' }
  }

  // Prefer Chinese colon first, then ': ' (avoid breaking URLs like http://)
  const separators = ['：', ': ']
  for (const sep of separators) {
    const idx = trimmed.indexOf(sep)
    if (idx > 0 && idx < trimmed.length - sep.length) {
      const title = trimmed.slice(0, idx).trim()
      const detail = trimmed.slice(idx + sep.length).trim()
      if (title && detail) {
        return { title, detail }
      }
    }
  }

  return { title: trimmed }
}

const normalizeDescriptionNode = (description?: ReactNode): ReactNode | undefined => {
  if (typeof description === 'string') {
    const trimmed = description.trim()
    if (!trimmed) return undefined
    return <div style={{ whiteSpace: 'pre-wrap', lineHeight: 1.5 }}>{trimmed}</div>
  }
  return description
}

export function showNotification(
  type: NoticeType,
  message: ReactNode,
  description?: ReactNode,
  options?: { placement?: NotificationPlacement; duration?: number; key?: string; durationMs?: number; meta?: Record<string, unknown> }
) {
  let normalizedMessage: ReactNode = message
  let normalizedDescription: ReactNode | undefined = description

  if (typeof message === 'string') {
    const { title, detail } = splitTitleAndDetail(message)
    normalizedMessage = <span style={{ fontWeight: 600 }}>{title}</span>

    if (normalizedDescription == null && detail) {
      normalizedDescription = detail
    }
  }

  normalizedDescription = normalizeDescriptionNode(normalizedDescription)
  return showNotificationFromHook(type, normalizedMessage, normalizedDescription, options)
}

export const notification = {
  success: (message: ReactNode, description?: ReactNode, duration?: number) => globalNotification.success(message, description, duration),
  error: (message: ReactNode, description?: ReactNode, duration?: number) => globalNotification.error(message, description, duration),
  warning: (message: ReactNode, description?: ReactNode, duration?: number) => globalNotification.warning(message, description, duration),
  info: (message: ReactNode, description?: ReactNode, duration?: number) => globalNotification.info(message, description, duration),
}

export default showNotification
