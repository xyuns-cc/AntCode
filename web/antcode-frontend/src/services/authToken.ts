let accessToken: string | null = null

export const setAccessToken = (token: string): void => {
  accessToken = token
}

export const getAccessToken = (): string | null => accessToken

export const clearAccessToken = (): void => {
  accessToken = null
}

// P1-round6 5.4: JWT payload 是 base64url 编码且可能含 Unicode; 普通 atob
// 遇到 `-`/`_` 会 InvalidCharacter, 也不会正确解 UTF-8。这里做 base64url →
// base64 归一化 + padding 补齐 + TextDecoder 解 UTF-8, 任何异常都吞成 null,
// 由调用方按未认证态处理(避免抛错触发错误的会话恢复)。
export const decodeAccessToken = (token: string): Record<string, unknown> | null => {
  try {
    const parts = token.split('.')
    if (parts.length < 2) return null
    const b64 = parts[1].replace(/-/g, '+').replace(/_/g, '/')
    const padded = b64 + '='.repeat((4 - (b64.length % 4)) % 4)
    const binary = atob(padded)
    const bytes = new Uint8Array(binary.length)
    for (let i = 0; i < binary.length; i++) bytes[i] = binary.charCodeAt(i)
    const text = new TextDecoder('utf-8', { fatal: false }).decode(bytes)
    return JSON.parse(text) as Record<string, unknown>
  } catch {
    return null
  }
}

// ---------------------------------------------------------------------------
// 会话提示标记（不含任何敏感信息）
// 登录成功时写入，登出/会话失效时清除；用于匿名首访时跳过 /auth/refresh 探测。
// 注意：key 刻意不含 auth/token/user 字样，避免被 AuthHandler.clearAuthData 的
// 模糊清理误删（该标记由本模块显式管理生命周期）。
// ---------------------------------------------------------------------------
const SESSION_HINT_STORAGE_KEY = 'antcode_session_hint'

export const setSessionHint = (): void => {
  try {
    localStorage.setItem(SESSION_HINT_STORAGE_KEY, '1')
  } catch {
    // ignore（隐私模式等场景下 localStorage 不可用）
  }
}

export const clearSessionHint = (): void => {
  try {
    localStorage.removeItem(SESSION_HINT_STORAGE_KEY)
  } catch {
    // ignore
  }
}

export const hasSessionHint = (): boolean => {
  try {
    return localStorage.getItem(SESSION_HINT_STORAGE_KEY) === '1'
  } catch {
    return false
  }
}

// ---------------------------------------------------------------------------
// 跨标签页认证事件（登录/登出同步）
// 首选 BroadcastChannel；不可用时降级为 localStorage storage 事件（只写时间戳
// 与事件类型，不含任何令牌等敏感信息）。
// ---------------------------------------------------------------------------
export type AuthBroadcastType = 'login' | 'logout'

export interface AuthBroadcastMessage {
  type: AuthBroadcastType
  at: number
}

const AUTH_CHANNEL_NAME = 'antcode-auth'
const AUTH_EVENT_STORAGE_KEY = 'antcode-cross-tab-event'

let broadcastChannel: BroadcastChannel | null = null

const getBroadcastChannel = (): BroadcastChannel | null => {
  if (typeof BroadcastChannel === 'undefined') return null
  if (!broadcastChannel) {
    broadcastChannel = new BroadcastChannel(AUTH_CHANNEL_NAME)
    // Node 环境（如 vitest）下不阻塞进程退出；浏览器中该方法不存在。
    ;(broadcastChannel as unknown as { unref?: () => void }).unref?.()
  }
  return broadcastChannel
}

const isAuthBroadcastMessage = (value: unknown): value is AuthBroadcastMessage => {
  if (!value || typeof value !== 'object') return false
  const type = (value as { type?: unknown }).type
  return type === 'login' || type === 'logout'
}

export const broadcastAuthEvent = (type: AuthBroadcastType): void => {
  const message: AuthBroadcastMessage = { type, at: Date.now() }
  try {
    const channel = getBroadcastChannel()
    if (channel) {
      channel.postMessage(message)
      return
    }
    // 降级：storage 事件只会触发其他标签页，不会回传给当前页
    localStorage.setItem(AUTH_EVENT_STORAGE_KEY, JSON.stringify(message))
  } catch {
    // ignore
  }
}

export const subscribeAuthEvents = (
  handler: (message: AuthBroadcastMessage) => void
): (() => void) => {
  const channel = getBroadcastChannel()
  if (channel) {
    const listener = (event: MessageEvent) => {
      if (isAuthBroadcastMessage(event.data)) {
        handler(event.data)
      }
    }
    channel.addEventListener('message', listener)
    return () => channel.removeEventListener('message', listener)
  }

  if (typeof window === 'undefined') {
    return () => undefined
  }

  const storageListener = (event: StorageEvent) => {
    if (event.key !== AUTH_EVENT_STORAGE_KEY || !event.newValue) return
    try {
      const parsed: unknown = JSON.parse(event.newValue)
      if (isAuthBroadcastMessage(parsed)) {
        handler(parsed)
      }
    } catch {
      // ignore
    }
  }
  window.addEventListener('storage', storageListener)
  return () => window.removeEventListener('storage', storageListener)
}
