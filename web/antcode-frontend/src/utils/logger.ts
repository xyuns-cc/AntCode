/* eslint-disable no-console */
/**
 * 日志工具类 - 统一管理控制台输出
 */
export class Logger {
  private static isDevelopment = process.env.NODE_ENV === 'development'
  
  static log(...args: unknown[]): void {
    if (this.isDevelopment) {
      console.log('[APP]', ...args)
    }
  }
  
  static warn(...args: unknown[]): void {
    if (this.isDevelopment) {
      console.warn('[WARN]', ...args)
    }
  }
  
  static error(...args: unknown[]): void {
    if (this.isDevelopment) {
      console.error('[ERROR]', ...args)
    }
  }
  
  static info(...args: unknown[]): void {
    if (this.isDevelopment) {
      console.info('[INFO]', ...args)
    }
  }
  
  static debug(...args: unknown[]): void {
    if (this.isDevelopment) {
      console.debug('[DEBUG]', ...args)
    }
  }
}

export default Logger
