import { globalAlert } from '@/components/common/AlertManager'

export type NoticeType = 'success' | 'error' | 'warning' | 'info'

// 兼容旧的API调用，转发到新的Alert系统
export function showNotification(
  type: NoticeType,
  message: string,
  description?: string,
  options?: {
    placement?: any
    duration?: number
    key?: string
    durationMs?: number // 兼容旧的 API
    meta?: Record<string, any> // 兼容 meta 数据，但不再处理
  }
) {
  // 处理持续时间
  const duration = options?.durationMs || options?.duration || (type === 'error' ? 0 : 4500)

  // 转发到新的Alert系统，只使用原始的description，忽略meta信息
  return globalAlert.show(type, message, description, duration)
}

// 兼容函数
export function configureNotifications(config: any) {
  // Alert系统不需要额外配置
  console.log('Alert system configuration applied')
}

export default showNotification