/**
 * WebSocket 连接管理器 - 生产环境优化版本
 * 支持心跳检测、自动重连、消息队列等特性
 */

import Logger from '@/utils/logger'
import { WS_BASE_URL } from '@/utils/constants'

// 连接状态
export const ConnectionState = {
  CONNECTING: 'connecting',
  CONNECTED: 'connected',
  RECONNECTING: 'reconnecting',
  DISCONNECTED: 'disconnected',
  FAILED: 'failed'
} as const

export type ConnectionStateType = typeof ConnectionState[keyof typeof ConnectionState]

// 消息类型
export interface WebSocketMessage {
  type: string
  data?: unknown
  timestamp?: string
  message?: string
  connection_id?: string
  config?: {
    ping_interval?: number
  }
  [key: string]: unknown
}

// 连接配置
export interface WebSocketConfig {
  url: string
  token?: string
  // 心跳配置
  pingInterval?: number      // 心跳间隔（毫秒）
  pongTimeout?: number       // pong 超时（毫秒）
  // 重连配置
  reconnect?: boolean        // 是否自动重连
  reconnectInterval?: number // 重连间隔（毫秒）
  maxReconnectAttempts?: number // 最大重连次数
  reconnectBackoff?: number  // 重连退避系数
  // 回调
  onOpen?: () => void
  onClose?: (event: CloseEvent) => void
  onError?: (error: Event | string) => void
  onMessage?: (message: WebSocketMessage) => void
  onStateChange?: (state: ConnectionStateType) => void
}

// 默认配置
const DEFAULT_CONFIG: Partial<WebSocketConfig> = {
  pingInterval: 25000,       // 25秒发送一次心跳
  pongTimeout: 10000,        // 10秒内未收到 pong 则认为断开
  reconnect: true,
  reconnectInterval: 1000,   // 初始重连间隔 1 秒
  maxReconnectAttempts: 10,
  reconnectBackoff: 1.5      // 每次重连间隔增加 1.5 倍
}

/**
 * WebSocket 连接管理器
 */
export class WebSocketManager {
  private ws: WebSocket | null = null
  private config: WebSocketConfig
  private state: ConnectionStateType = ConnectionState.DISCONNECTED
  
  // 心跳相关
  private pingTimer: ReturnType<typeof setInterval> | null = null
  private pongTimer: ReturnType<typeof setTimeout> | null = null
  
  // 重连相关
  private reconnectAttempts: number = 0
  private reconnectTimer: ReturnType<typeof setTimeout> | null = null
  private manualClose: boolean = false
  
  // 消息队列（断线时缓存消息）
  private messageQueue: WebSocketMessage[] = []
  private maxQueueSize: number = 100
  
  constructor(config: WebSocketConfig) {
    this.config = { ...DEFAULT_CONFIG, ...config }
  }
  
  /**
   * 连接 WebSocket
   */
  connect(): void {
    if (this.ws && (this.ws.readyState === WebSocket.CONNECTING || this.ws.readyState === WebSocket.OPEN)) {
      Logger.warn('WebSocket 已连接或正在连接中')
      return
    }
    
    this.manualClose = false
    this.setState(ConnectionState.CONNECTING)
    
    try {
      // 构建 URL
      let url = this.config.url
      if (this.config.token) {
        const separator = url.includes('?') ? '&' : '?'
        url = `${url}${separator}token=${encodeURIComponent(this.config.token)}`
      }
      
      this.ws = new WebSocket(url)
      this.setupEventHandlers()
      
    } catch (error) {
      Logger.error('创建 WebSocket 失败:', error)
      this.setState(ConnectionState.FAILED)
      this.config.onError?.(error as Event)
    }
  }
  
  /**
   * 断开连接
   */
  disconnect(code: number = 1000, reason: string = '客户端主动断开'): void {
    this.manualClose = true
    this.stopHeartbeat()
    this.stopReconnect()
    
    if (this.ws) {
      try {
        this.ws.close(code, reason)
      } catch {
        // 忽略关闭错误
      }
      this.ws = null
    }
    
    this.setState(ConnectionState.DISCONNECTED)
  }
  
  /**
   * 发送消息
   */
  send(message: WebSocketMessage): boolean {
    if (this.ws && this.ws.readyState === WebSocket.OPEN) {
      try {
        this.ws.send(JSON.stringify(message))
        return true
      } catch (error) {
        Logger.error('发送消息失败:', error)
        return false
      }
    } else {
      // 连接未就绪，加入队列
      if (this.messageQueue.length < this.maxQueueSize) {
        this.messageQueue.push(message)
      }
      return false
    }
  }
  
  /**
   * 获取当前状态
   */
  getState(): ConnectionStateType {
    return this.state
  }
  
  /**
   * 是否已连接
   */
  isConnected(): boolean {
    return this.state === ConnectionState.CONNECTED && 
           this.ws !== null && 
           this.ws.readyState === WebSocket.OPEN
  }
  
  // ==================== 私有方法 ====================
  
  private setupEventHandlers(): void {
    if (!this.ws) return
    
    this.ws.onopen = () => {
      Logger.info('WebSocket 连接已建立')
      this.reconnectAttempts = 0
      this.setState(ConnectionState.CONNECTED)
      this.startHeartbeat()
      this.flushMessageQueue()
      this.config.onOpen?.()
    }
    
    this.ws.onclose = (event) => {
      Logger.info(`WebSocket 连接关闭: ${event.code} - ${event.reason}`)
      this.stopHeartbeat()
      
      if (!this.manualClose && this.config.reconnect) {
        this.scheduleReconnect()
      } else {
        this.setState(ConnectionState.DISCONNECTED)
      }
      
      this.config.onClose?.(event)
    }
    
    this.ws.onerror = (error) => {
      Logger.error('WebSocket 错误:', error)
      this.config.onError?.(error)
    }
    
    this.ws.onmessage = (event) => {
      try {
        const message = JSON.parse(event.data) as WebSocketMessage
        this.handleMessage(message)
      } catch (error) {
        Logger.error('解析 WebSocket 消息失败:', error)
      }
    }
  }
  
  private handleMessage(message: WebSocketMessage): void {
    // 处理心跳响应
    if (message.type === 'pong') {
      this.handlePong()
      return
    }
    
    // 处理服务器心跳
    if (message.type === 'ping') {
      this.send({ type: 'pong', timestamp: new Date().toISOString() })
      return
    }
    
    // 处理连接确认
    if (message.type === 'connected') {
      Logger.info('收到服务器连接确认:', message.connection_id)
      // 可以从服务器获取心跳配置
      if (message.config?.ping_interval) {
        // 使用服务器配置的心跳间隔（转换为毫秒）
        this.config.pingInterval = message.config.ping_interval * 1000 * 0.8 // 比服务器间隔稍短
      }
      return
    }
    
    // 处理错误消息
    if (message.type === 'error') {
      Logger.error('服务器错误:', message.message || message.data)
      this.config.onError?.(message.message || '服务器错误')
      return
    }
    
    // 其他消息传递给回调
    this.config.onMessage?.(message)
  }
  
  private startHeartbeat(): void {
    this.stopHeartbeat()
    
    const interval = this.config.pingInterval || 25000
    
    this.pingTimer = setInterval(() => {
      if (this.isConnected()) {
        this.sendPing()
      }
    }, interval)
    
    Logger.debug(`心跳已启动，间隔: ${interval}ms`)
  }
  
  private stopHeartbeat(): void {
    if (this.pingTimer) {
      clearInterval(this.pingTimer)
      this.pingTimer = null
    }
    if (this.pongTimer) {
      clearTimeout(this.pongTimer)
      this.pongTimer = null
    }
  }
  
  private sendPing(): void {
    const sent = this.send({
      type: 'pong',  // 响应服务器的 ping
      timestamp: new Date().toISOString()
    })
    
    if (sent) {
      // 设置 pong 超时检测
      this.pongTimer = setTimeout(() => {
        Logger.warn('心跳超时，准备重连')
        this.ws?.close(4008, '心跳超时')
      }, this.config.pongTimeout || 10000)
    }
  }
  
  private handlePong(): void {
    if (this.pongTimer) {
      clearTimeout(this.pongTimer)
      this.pongTimer = null
    }
  }
  
  private scheduleReconnect(): void {
    if (this.reconnectAttempts >= (this.config.maxReconnectAttempts || 10)) {
      Logger.error('达到最大重连次数，停止重连')
      this.setState(ConnectionState.FAILED)
      return
    }
    
    this.setState(ConnectionState.RECONNECTING)
    
    // 计算重连延迟（指数退避）
    const baseInterval = this.config.reconnectInterval || 1000
    const backoff = this.config.reconnectBackoff || 1.5
    const delay = Math.min(baseInterval * Math.pow(backoff, this.reconnectAttempts), 30000)
    
    Logger.info(`将在 ${delay}ms 后进行第 ${this.reconnectAttempts + 1} 次重连`)
    
    this.reconnectTimer = setTimeout(() => {
      this.reconnectAttempts++
      this.connect()
    }, delay)
  }
  
  private stopReconnect(): void {
    if (this.reconnectTimer) {
      clearTimeout(this.reconnectTimer)
      this.reconnectTimer = null
    }
    this.reconnectAttempts = 0
  }
  
  private flushMessageQueue(): void {
    while (this.messageQueue.length > 0 && this.isConnected()) {
      const message = this.messageQueue.shift()
      if (message) {
        this.send(message)
      }
    }
  }
  
  private setState(state: ConnectionStateType): void {
    if (this.state !== state) {
      this.state = state
      this.config.onStateChange?.(state)
    }
  }
}

/**
 * 创建日志 WebSocket 连接
 */
export function createLogWebSocket(
  runId: string,
  callbacks: {
    onMessage?: (message: WebSocketMessage) => void
    onStateChange?: (state: ConnectionStateType) => void
    onError?: (error: Event | string) => void
  }
): WebSocketManager | null {
  const token = localStorage.getItem('access_token')
  if (!token) {
    Logger.error('未找到访问令牌')
    callbacks.onError?.('未找到访问令牌')
    return null
  }
  
  // 构建 WebSocket URL
  const wsBase = WS_BASE_URL.replace(/\/$/, '')
  const url = `${wsBase}/api/v1/ws/runs/${runId}/logs`
  
  const manager = new WebSocketManager({
    url,
    token,
    reconnect: true,
    maxReconnectAttempts: 5,
    onMessage: callbacks.onMessage,
    onStateChange: callbacks.onStateChange,
    onError: callbacks.onError
  })
  
  manager.connect()
  return manager
}

export default WebSocketManager
