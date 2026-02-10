// 格式化工具函数

/**
 * 格式化日期时间
 */
export function formatDateTime(dateString: string | Date, format: string = 'YYYY-MM-DD HH:mm:ss'): string {
  if (!dateString) return '-'
  
  const date = typeof dateString === 'string' ? new Date(dateString) : dateString
  
  if (isNaN(date.getTime())) return '-'
  
  const year = date.getFullYear()
  const month = String(date.getMonth() + 1).padStart(2, '0')
  const day = String(date.getDate()).padStart(2, '0')
  const hours = String(date.getHours()).padStart(2, '0')
  const minutes = String(date.getMinutes()).padStart(2, '0')
  const seconds = String(date.getSeconds()).padStart(2, '0')
  
  return format
    .replace('YYYY', String(year))
    .replace('MM', month)
    .replace('DD', day)
    .replace('HH', hours)
    .replace('mm', minutes)
    .replace('ss', seconds)
}

/**
 * 格式化日期
 */
export function formatDate(dateString: string | Date): string {
  return formatDateTime(dateString, 'YYYY-MM-DD')
}

/**
 * 格式化时间
 */
export function formatTime(dateString: string | Date): string {
  return formatDateTime(dateString, 'HH:mm:ss')
}

/**
 * 格式化相对时间
 */
export function formatRelativeTime(dateString: string | Date): string {
  if (!dateString) return '-'
  
  const date = typeof dateString === 'string' ? new Date(dateString) : dateString
  const now = new Date()
  const diff = now.getTime() - date.getTime()
  
  const seconds = Math.floor(diff / 1000)
  const minutes = Math.floor(seconds / 60)
  const hours = Math.floor(minutes / 60)
  const days = Math.floor(hours / 24)
  
  if (days > 0) {
    return `${days}天前`
  } else if (hours > 0) {
    return `${hours}小时前`
  } else if (minutes > 0) {
    return `${minutes}分钟前`
  } else if (seconds > 0) {
    return `${seconds}秒前`
  } else {
    return '刚刚'
  }
}

/**
 * 格式化持续时间
 */
export function formatDuration(seconds: number): string {
  if (!seconds || seconds < 0) return '-'
  
  const hours = Math.floor(seconds / 3600)
  const minutes = Math.floor((seconds % 3600) / 60)
  const remainingSeconds = Math.floor(seconds % 60)
  
  if (hours > 0) {
    return `${hours}小时${minutes}分钟${remainingSeconds}秒`
  } else if (minutes > 0) {
    return `${minutes}分钟${remainingSeconds}秒`
  } else {
    return `${remainingSeconds}秒`
  }
}

/**
 * 格式化文件大小
 */
export function formatFileSize(bytes: number): string {
  if (!bytes || bytes === 0) return '0 B'
  
  const sizes = ['B', 'KB', 'MB', 'GB', 'TB']
  const i = Math.floor(Math.log(bytes) / Math.log(1024))
  
  return `${(bytes / Math.pow(1024, i)).toFixed(2)} ${sizes[i]}`
}

/**
 * 格式化数字
 */
export function formatNumber(num: number, decimals: number = 0): string {
  if (typeof num !== 'number' || isNaN(num)) return '-'
  
  return num.toLocaleString('zh-CN', {
    minimumFractionDigits: decimals,
    maximumFractionDigits: decimals
  })
}

/**
 * 格式化百分比
 */
export function formatPercentage(value: number, total: number, decimals: number = 1): string {
  if (!total || total === 0) return '0%'
  
  const percentage = (value / total) * 100
  return `${percentage.toFixed(decimals)}%`
}

/**
 * 格式化货币
 */
export function formatCurrency(amount: number, currency: string = 'CNY'): string {
  if (typeof amount !== 'number' || isNaN(amount)) return '-'
  
  return new Intl.NumberFormat('zh-CN', {
    style: 'currency',
    currency: currency
  }).format(amount)
}

/**
 * 截断文本
 */
export function truncateText(text: string, maxLength: number = 50): string {
  if (!text || text.length <= maxLength) return text
  
  return text.substring(0, maxLength) + '...'
}

/**
 * 格式化JSON
 */
export function formatJSON<T>(obj: T, indent: number = 2): string {
  try {
    return JSON.stringify(obj, null, indent)
  } catch {
    return String(obj)
  }
}

/**
 * 解析JSON
 */
export function parseJSON<T = unknown>(jsonString: string): T | null {
  try {
    return JSON.parse(jsonString) as T
  } catch {
    return null
  }
}

/**
 * 格式化状态文本
 */
export function formatStatus(status: string): { text: string; color: string } {
  const statusMap: Record<string, { text: string; color: string }> = {
    // 任务执行状态
    pending: { text: '等待调度', color: 'default' },
    dispatching: { text: '分配 Worker 中', color: 'processing' },
    queued: { text: '排队中', color: 'cyan' },
    running: { text: '执行中', color: 'processing' },
    success: { text: '成功', color: 'success' },
    failed: { text: '失败', color: 'error' },
    cancelled: { text: '已取消', color: 'warning' },
    timeout: { text: '超时', color: 'error' },
    paused: { text: '已暂停', color: 'warning' },
    rejected: { text: '已拒绝', color: 'error' },
    skipped: { text: '已跳过', color: 'default' },
    // 通用状态
    active: { text: '活跃', color: 'success' },
    inactive: { text: '非活跃', color: 'default' },
    // Worker 状态
    online: { text: '在线', color: 'success' },
    offline: { text: '离线', color: 'default' },
    maintenance: { text: '维护中', color: 'warning' }
  }
  
  return statusMap[status] || { text: status, color: 'default' }
}

/**
 * 格式化优先级
 */
export function formatPriority(priority: string): { text: string; color: string } {
  const priorityMap: Record<string, { text: string; color: string }> = {
    low: { text: '低', color: 'default' },
    normal: { text: '普通', color: 'blue' },
    high: { text: '高', color: 'orange' },
    urgent: { text: '紧急', color: 'red' }
  }
  
  return priorityMap[priority] || { text: priority, color: 'default' }
}

/**
 * 格式化任务类型
 */
export function formatTaskType(type: string): { text: string; color: string } {
  const typeMap: Record<string, { text: string; color: string }> = {
    code: { text: '代码任务', color: 'blue' },
    rule: { text: '规则任务', color: 'green' },
    manual: { text: '手动任务', color: 'default' },
    scheduled: { text: '定时任务', color: 'purple' }
  }
  
  return typeMap[type] || { text: type, color: 'default' }
}
