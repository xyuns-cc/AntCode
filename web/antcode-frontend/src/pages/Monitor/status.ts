import type { WorkerDisplayStatus, WorkerOs, WorkerLog } from './types'

export const getStatusColor = (status: string): string => {
  if (status === 'running' || status === 'success') return 'success'
  if (status === 'warning') return 'warning'
  if (status === 'error' || status === 'failed') return 'error'
  if (status === 'pending') return 'processing'
  return 'default'
}

export const getStatusText = (status: string): string => {
  const labels: Record<string, string> = {
    running: '运行中',
    warning: '需注意',
    error: '异常',
    stopped: '已停止',
    success: '成功',
    failed: '失败',
    pending: '待执行',
  }
  return labels[status] || '未知'
}

export const getOsName = (os: WorkerOs): string => {
  const labels: Record<WorkerOs, string> = {
    windows: 'Windows Server',
    ubuntu: 'Ubuntu',
    debian: 'Debian',
    centos: 'CentOS',
    redhat: 'Red Hat',
    alpine: 'Alpine Linux',
    fedora: 'Fedora',
    macos: 'macOS',
    linux: 'Linux',
  }
  return labels[os]
}

export const getLogTypeColor = (type: WorkerLog['type']): string => {
  if (type === 'error') return 'error'
  if (type === 'warning') return 'warning'
  if (type === 'success') return 'success'
  return 'default'
}

export const getLogTypeText = (type: WorkerLog['type']): string => {
  const labels: Record<WorkerLog['type'], string> = {
    error: '错误',
    warning: '警告',
    info: '信息',
    success: '成功',
  }
  return labels[type]
}

export const getSystemStatus = (status: WorkerDisplayStatus | 'normal') => {
  if (status === 'error') return { badge: 'error' as const, text: '系统异常' }
  if (status === 'warning') return { badge: 'warning' as const, text: '需要关注' }
  return { badge: 'success' as const, text: '系统正常' }
}
