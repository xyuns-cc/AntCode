/**
 * WebSocket 相关类型定义
 */

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
