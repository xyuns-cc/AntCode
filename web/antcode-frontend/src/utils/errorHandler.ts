import showNotification from '@/utils/notification'
import axios from 'axios'
import type { AxiosError } from 'axios'
import Logger from './logger'

// 错误类型定义
export interface ApiError {
  code: string
  message: string
  details?: Record<string, unknown>
  status?: number
  timestamp?: string
}

export interface ErrorContext {
  action?: string
  component?: string
  userId?: string
  url?: string
  method?: string
}

// 错误级别
export const ErrorLevel = {
  INFO: 'info',
  WARNING: 'warning',
  ERROR: 'error',
  CRITICAL: 'critical'
} as const

export type ErrorLevel = (typeof ErrorLevel)[keyof typeof ErrorLevel]

// 错误处理配置
interface ErrorHandlerConfig {
  showMessage?: boolean
  showNotification?: boolean
  logError?: boolean
  reportError?: boolean
  level?: ErrorLevel
  duration?: number
}

class ErrorHandler {
  private static instance: ErrorHandler
  private errorQueue: ApiError[] = []
  private maxQueueSize = 100

  private constructor() {}

  static getInstance(): ErrorHandler {
    if (!ErrorHandler.instance) {
      ErrorHandler.instance = new ErrorHandler()
    }
    return ErrorHandler.instance
  }

  // 处理API错误
  handleApiError(
    error: AxiosError | Error | unknown,
    context?: ErrorContext,
    config: ErrorHandlerConfig = {}
  ): ApiError {
    const {
      showMessage = true,
      showNotification = false,
      logError = true,
      reportError = true,
      level = ErrorLevel.ERROR,
      duration = 4.5
    } = config

    const apiError = this.parseError(error, context)

    // 记录错误日志
    if (logError) {
      this.logError(apiError, context, level)
    }

    // 显示用户友好的错误提示
    if (showMessage) {
      this.showErrorMessage(apiError, duration, context)
    }

    if (showNotification) {
      this.showErrorNotification(apiError, duration, context)
    }

    // 上报错误
    if (reportError) {
      this.reportError(apiError, context)
    }

    // 添加到错误队列
    this.addToQueue(apiError)

    return apiError
  }

  // 解析错误
  private parseError(error: unknown, context?: ErrorContext): ApiError {
    let apiError: ApiError = {
      code: 'UNKNOWN_ERROR',
      message: '未知错误',
      timestamp: new Date().toISOString()
    }

    if (axios.isAxiosError(error)) {
      // HTTP错误
      const response = error.response
      const request = error.request

      if (response) {
        // 服务器响应错误
        apiError = {
          code: response.data?.code || `HTTP_${response.status}`,
          message: this.getHttpErrorMessage(response.status, response.data?.message),
          details: response.data?.details || response.data,
          status: response.status,
          timestamp: new Date().toISOString()
        }
      } else if (request) {
        // 网络错误
        apiError = {
          code: 'NETWORK_ERROR',
          message: '网络连接失败，请检查网络设置',
          details: { originalMessage: error.message },
          timestamp: new Date().toISOString()
        }
      } else {
        // 请求配置错误
        apiError = {
          code: 'REQUEST_CONFIG_ERROR',
          message: '请求配置错误',
          details: { originalMessage: error.message },
          timestamp: new Date().toISOString()
        }
      }
    } else if (error instanceof Error) {
      // 普通JavaScript错误
      apiError = {
        code: error.name || 'JS_ERROR',
        message: error.message || '应用程序错误',
        details: { stack: error.stack },
        timestamp: new Date().toISOString()
      }
    } else if (typeof error === 'string') {
      // 字符串错误
      apiError = {
        code: 'STRING_ERROR',
        message: error,
        timestamp: new Date().toISOString()
      }
    } else if (error && typeof error === 'object') {
      // 对象错误
      const errorObj = error as { code?: string; message?: string; details?: Record<string, unknown>; status?: number }
      apiError = {
        code: errorObj.code || 'OBJECT_ERROR',
        message: errorObj.message || '对象错误',
        details: errorObj.details || errorObj,
        status: errorObj.status,
        timestamp: new Date().toISOString()
      }
    }

    if (context) {
      apiError = {
        ...apiError,
        details: {
          ...(apiError.details || {}),
          context,
        }
      }
    }

    return apiError
  }

  // 获取HTTP错误消息
  private getHttpErrorMessage(status: number, serverMessage?: string): string {
    if (serverMessage) return serverMessage

    const statusMessages: Record<number, string> = {
      400: '请求参数错误',
      401: '未授权，请重新登录',
      403: '没有权限访问该资源',
      404: '请求的资源不存在',
      408: '请求超时',
      409: '请求冲突',
      422: '请求参数验证失败',
      429: '请求过于频繁，请稍后重试',
      500: '服务器内部错误',
      502: '网关错误',
      503: '服务暂时不可用',
      504: '网关超时'
    }

    return statusMessages[status] || `HTTP错误 ${status}`
  }

  // 记录错误日志
  private logError(error: ApiError, context?: ErrorContext, level: ErrorLevel = ErrorLevel.ERROR) {
    const logData = {
      error,
      context,
      level,
      timestamp: new Date().toISOString(),
      userAgent: navigator.userAgent,
      url: window.location.href
    }

    switch (level) {
      case ErrorLevel.INFO:
        Logger.info('API Error (INFO):', logData)
        break
      case ErrorLevel.WARNING:
        Logger.warn('API Error (WARNING):', logData)
        break
      case ErrorLevel.ERROR:
        Logger.error('API Error (ERROR):', logData)
        break
      case ErrorLevel.CRITICAL:
        Logger.error('API Error (CRITICAL):', logData)
        break
    }
  }

  // 显示错误消息
  private showErrorMessage(error: ApiError, duration: number, context?: ErrorContext) {
    const messageType = this.getMessageType(error.status)
    showNotification(messageType, error.message, undefined, {
      duration,
      meta: {
        状态码: error.status,
        时间: error.timestamp,
        动作: context?.action,
        组件: context?.component,
        方法: context?.method,
        接口: context?.url,
      }
    })
  }

  // 显示错误通知
  private showErrorNotification(error: ApiError, duration: number, context?: ErrorContext) {
    const notificationType = this.getMessageType(error.status)
    showNotification(notificationType, '操作失败', error.message, {
      duration,
      meta: {
        状态码: error.status,
        动作: context?.action,
        组件: context?.component,
        方法: context?.method,
        接口: context?.url,
      }
    })
  }

  // 获取消息类型
  private getMessageType(status?: number): 'error' | 'warning' | 'info' {
    if (!status) return 'error'
    
    if (status >= 500) return 'error'
    if (status >= 400) return 'warning'
    return 'info'
  }

  // 上报错误
  private reportError(error: ApiError, context?: ErrorContext) {
    // 这里可以集成错误监控服务
    const errorReport = {
      ...error,
      context,
      userAgent: navigator.userAgent,
      url: window.location.href,
      timestamp: new Date().toISOString()
    }

    // 示例：发送到错误监控服务
    // if (window.Sentry) {
    //   window.Sentry.captureException(new Error(error.message), {
    //     tags: {
    //       errorCode: error.code,
    //       errorStatus: error.status?.toString()
    //     },
    //     extra: errorReport
    //   })
    // }

    Logger.info('Error reported:', errorReport)
  }

  // 添加到错误队列
  private addToQueue(error: ApiError) {
    this.errorQueue.push(error)
    
    // 保持队列大小
    if (this.errorQueue.length > this.maxQueueSize) {
      this.errorQueue.shift()
    }
  }

  // 获取错误历史
  getErrorHistory(): ApiError[] {
    return [...this.errorQueue]
  }

  // 清空错误历史
  clearErrorHistory(): void {
    this.errorQueue = []
  }

  // 获取错误统计
  getErrorStats(): Record<string, number> {
    const stats: Record<string, number> = {}
    
    this.errorQueue.forEach(error => {
      stats[error.code] = (stats[error.code] || 0) + 1
    })
    
    return stats
  }
}

// 导出单例实例
export const errorHandler = ErrorHandler.getInstance()

// 便捷方法
export const handleApiError = (
  error: unknown,
  context?: ErrorContext,
  config?: ErrorHandlerConfig
) => {
  return errorHandler.handleApiError(error, context, config)
}

// 特定场景的错误处理
export const handleAuthError = (error: unknown) => {
  return handleApiError(error, { action: 'authentication' }, {
    showMessage: true,
    level: ErrorLevel.WARNING
  })
}

export const handleNetworkError = (error: unknown) => {
  return handleApiError(error, { action: 'network' }, {
    showMessage: true,
    showNotification: true,
    level: ErrorLevel.ERROR
  })
}

export const handleValidationError = (error: unknown) => {
  return handleApiError(error, { action: 'validation' }, {
    showMessage: true,
    level: ErrorLevel.WARNING,
    duration: 6
  })
}

export default errorHandler
