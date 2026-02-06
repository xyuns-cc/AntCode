/**
 * 防抖函数
 */
export const debounce = <T extends (...args: unknown[]) => unknown>(
  func: T,
  wait: number
): ((...args: Parameters<T>) => void) => {
  let timeout: NodeJS.Timeout | null = null
  
  return (...args: Parameters<T>) => {
    if (timeout) clearTimeout(timeout)
    timeout = setTimeout(() => func(...args), wait)
  }
}

/**
 * 节流函数
 */
export const throttle = <T extends (...args: unknown[]) => unknown>(
  func: T,
  limit: number
): ((...args: Parameters<T>) => void) => {
  let inThrottle: boolean
  
  return (...args: Parameters<T>) => {
    if (!inThrottle) {
      func(...args)
      inThrottle = true
      setTimeout(() => (inThrottle = false), limit)
    }
  }
}

/**
 * 生成随机ID
 */
export const generateId = (): string => {
  return Math.random().toString(36).substr(2, 9)
}

/**
 * 深拷贝对象
 */
export const deepClone = <T>(obj: T): T => {
  if (obj === null || typeof obj !== 'object') return obj
  if (obj instanceof Date) return new Date(obj.getTime()) as unknown as T
  if (obj instanceof Array) return obj.map(item => deepClone(item)) as unknown as T
  if (typeof obj === 'object') {
    const clonedObj = {} as T
    for (const key in obj) {
      if (Object.prototype.hasOwnProperty.call(obj, key)) {
        clonedObj[key] = deepClone(obj[key])
      }
    }
    return clonedObj
  }
  return obj
}

/**
 * 检查是否为空值
 */
export const isEmpty = (value: unknown): boolean => {
  if (value === null || value === undefined) return true
  if (typeof value === 'string') return value.trim() === ''
  if (Array.isArray(value)) return value.length === 0
  if (typeof value === 'object') return Object.keys(value).length === 0
  return false
}

/**
 * 安全的JSON解析
 */
export const safeJsonParse = <T = unknown>(str: string, defaultValue: T): T => {
  try {
    return JSON.parse(str)
  } catch {
    return defaultValue
  }
}

/**
 * 获取错误消息
 */
export const getErrorMessage = (error: unknown): string => {
  if (typeof error === 'string') return error
  if (error && typeof error === 'object') {
    const errorObj = error as {
      response?: { data?: { detail?: string; message?: string } }
      message?: string
    }
    if (errorObj.response?.data?.detail) return errorObj.response.data.detail
    if (errorObj.response?.data?.message) return errorObj.response.data.message
    if (errorObj.message) return errorObj.message
  }
  return '未知错误'
}

/**
 * 判断请求是否被主动取消
 */
export const isAbortError = (error: unknown): boolean => {
  if (!error || typeof error !== 'object') return false
  const err = error as { code?: string; name?: string }
  return err.code === 'ERR_CANCELED' || err.name === 'CanceledError'
}
