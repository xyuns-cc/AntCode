import type { MessageInstance } from 'antd/es/message/interface'
import type { NotificationInstance } from 'antd/es/notification/interface'
import type { ModalStaticFunctions } from 'antd/es/modal/confirm'
import type { ReactNode } from 'react'

let messageInstance: MessageInstance | null = null
let notificationInstance: NotificationInstance | null = null
let modalInstance: Omit<ModalStaticFunctions, 'warn'> | null = null

/** Set global instances - called by AppInitializer */
export const setMessageInstances = (
  message: MessageInstance,
  notification: NotificationInstance,
  modal: Omit<ModalStaticFunctions, 'warn'>
) => {
  messageInstance = message
  notificationInstance = notification
  modalInstance = modal
}

/** Global message API */
export const globalMessage = {
  success: (content: string, duration?: number) => 
    messageInstance?.success({ content, duration: duration ?? 3 }),
  error: (content: string, duration?: number) => 
    messageInstance?.error({ content, duration: duration ?? 5 }),
  warning: (content: string, duration?: number) => 
    messageInstance?.warning({ content, duration: duration ?? 4 }),
  info: (content: string, duration?: number) => 
    messageInstance?.info({ content, duration: duration ?? 3 }),
  loading: (content: string, duration?: number) => 
    messageInstance?.loading({ content, duration: duration ?? 0 }),
  destroy: () => messageInstance?.destroy(),
}

/** Global notification API */
export const globalNotification = {
  success: (message: ReactNode, description?: ReactNode, duration?: number) =>
    notificationInstance?.success({ message, description, duration: duration ?? 3, placement: 'topRight' }),
  error: (message: ReactNode, description?: ReactNode, duration?: number) =>
    notificationInstance?.error({ message, description, duration: duration ?? 5, placement: 'topRight' }),
  warning: (message: ReactNode, description?: ReactNode, duration?: number) =>
    notificationInstance?.warning({ message, description, duration: duration ?? 4, placement: 'topRight' }),
  info: (message: ReactNode, description?: ReactNode, duration?: number) =>
    notificationInstance?.info({ message, description, duration: duration ?? 3, placement: 'topRight' }),
  destroy: () => notificationInstance?.destroy(),
}

/** Global modal API */
export const globalModal = {
  confirm: (props: Parameters<ModalStaticFunctions['confirm']>[0]) => modalInstance?.confirm(props),
  info: (props: Parameters<ModalStaticFunctions['info']>[0]) => modalInstance?.info(props),
  success: (props: Parameters<ModalStaticFunctions['success']>[0]) => modalInstance?.success(props),
  error: (props: Parameters<ModalStaticFunctions['error']>[0]) => modalInstance?.error(props),
  warning: (props: Parameters<ModalStaticFunctions['warning']>[0]) => modalInstance?.warning(props),
}

export type NoticeType = 'success' | 'error' | 'warning' | 'info'

export type NotificationPlacement = 'topLeft' | 'topRight' | 'bottomLeft' | 'bottomRight'

export function showNotification(
  type: NoticeType,
  message: ReactNode,
  description?: ReactNode,
  options?: { duration?: number; durationMs?: number; placement?: NotificationPlacement; key?: string }
) {
  const defaultDurations: Record<NoticeType, number> = {
    success: 3,
    error: 5,
    warning: 4,
    info: 3
  }
  const duration = options?.durationMs 
    ? options.durationMs / 1000 
    : options?.duration ?? defaultDurations[type]

  const placement = options?.placement ?? 'topRight'
  const key = options?.key
  return notificationInstance?.[type]({ message, description, duration, placement, key })
}
