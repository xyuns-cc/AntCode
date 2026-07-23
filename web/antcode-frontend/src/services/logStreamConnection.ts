import Logger from '@/utils/logger'
import { API_BASE_URL } from '@/utils/constants'
import type {
  LogStreamConnection,
  LogStreamOptions,
} from './logs'
import { broadcastAuthEvent } from './authToken'
import { buildLogStreamUrl } from './logStreamUrl'
import { MAX_RECONNECT_ATTEMPTS, reconnectDelayMs } from './logStreamBackoff'
import { toStatusUpdate, toStreamLogEntry } from './logStreamMessages'
import {
  isPermanentTicketError,
  isValidStreamPayload,
  parseStreamPayload,
  type StreamPayload,
} from './logStreamProtocol'

const WATCHDOG_STALE_MS = 45_000
const WATCHDOG_CHECK_MS = 15_000
const PERMANENT_TICKET_ERROR_STATUSES = new Set([400, 401, 403, 404])

export class EventSourceLogConnection implements LogStreamConnection {
  private source: EventSource | null = null
  private reconnectTimer: ReturnType<typeof setTimeout> | null = null
  private watchdog: ReturnType<typeof setInterval> | null = null
  private generation = 0
  private reconnectAttempts = 0
  private lastEventAt = Date.now()
  private lastEventId = ''
  private historyComplete = false
  private connecting = false
  private closed = false
  private readonly getTicket: () => Promise<string>
  private readonly options: LogStreamOptions

  constructor(
    getTicket: () => Promise<string>,
    options: LogStreamOptions,
  ) {
    this.getTicket = getTicket
    this.options = options
  }

  start(): void {
    void this.connect()
  }

  disconnect(): void {
    this.closed = true
    this.generation += 1
    this.clearReconnectTimer()
    this.closeSource()
  }

  private async connect(): Promise<void> {
    if (this.closed || this.connecting || this.source) return
    this.connecting = true
    const generation = ++this.generation
    try {
      const ticket = await this.getTicket()
      if (!this.isCurrent(generation)) return
      this.openSource(ticket, generation)
    } catch (error) {
      if (!this.isCurrent(generation)) return
      this.options.onStateChange?.('error')
      this.options.onError?.(error)
      if (isPermanentTicketError(error, PERMANENT_TICKET_ERROR_STATUSES)) {
        this.closed = true
        this.options.onStateChange?.('failed')
        return
      }
      this.scheduleReconnect()
    } finally {
      if (generation === this.generation) this.connecting = false
    }
  }

  private openSource(ticket: string, generation: number): void {
    const url = buildLogStreamUrl({
      apiBaseUrl: API_BASE_URL,
      runId: this.options.runId,
      ticket,
      cursor: this.lastEventId,
    })
    const source = new EventSource(url)
    this.source = source
    this.historyComplete = Boolean(this.lastEventId)
    this.touch()
    source.onopen = () => {
      if (!this.isActive(source, generation)) return
      this.touch()
      this.options.onStateChange?.('connected')
    }
    this.registerEventHandlers(source, generation)
    source.onerror = () => this.handleSourceError(source, generation)
    this.startWatchdog(source, generation)
  }

  private registerEventHandlers(source: EventSource, generation: number): void {
    this.listen(source, generation, 'log_line', (payload) => {
      this.markRealtimeHealthy()
      this.emitLog(payload)
    })
    // run_status 是握手帧（服务端连接后固定先发），不能作为“连接成功”信号清零熔断计数，
    // 否则 recovery_unavailable 等换票循环永远达不到熔断阈值。
    this.listen(source, generation, 'run_status', (payload) => this.emitStatus(payload))
    this.listen(source, generation, 'historical_logs_start', () => {
      this.historyComplete = false
      this.options.onHistoricalLogsUpdate?.({ phase: 'loading' })
    })
    this.listen(source, generation, 'historical_logs_end', (payload) => {
      this.historyComplete = true
      const sentLines = Number(payload.sent_lines)
      this.options.onHistoricalLogsUpdate?.({
        phase: 'loaded',
        sentLines: Number.isFinite(sentLines) ? sentLines : 0,
        truncated: payload.truncated === true,
      })
    })
    this.listen(source, generation, 'no_historical_logs', (payload) => {
      this.historyComplete = true
      this.options.onHistoricalLogsUpdate?.({
        phase: 'empty', sentLines: 0, truncated: payload.truncated === true,
      })
    })
    this.listen(source, generation, 'stream_cursor', () => undefined)
    this.listen(source, generation, 'ping', () => this.markConnectionHealthy())
    this.listen(source, generation, 'stream_error', (payload) => {
      this.handleStreamError(source, generation, payload)
    })
  }

  private listen(
    source: EventSource,
    generation: number,
    type: string,
    handler: (payload: StreamPayload) => void,
  ): void {
    source.addEventListener(type, (event) => {
      if (!this.isActive(source, generation)) return
      this.touch()
      const messageEvent = event as MessageEvent
      const payload = this.parseData(messageEvent)
      if (!payload || !isValidStreamPayload(type, payload)) {
        this.handleCorruptFrame(source, generation, type)
        return
      }
      this.recordLastEventId(messageEvent)
      handler(payload)
    })
  }

  /** 无效帧视为流损坏：终止当前流并走重连路径，禁止后续 checkpoint 越过未验证帧。 */
  private handleCorruptFrame(source: EventSource, generation: number, type: string): void {
    this.options.onError?.(new Error(`SSE ${type} 帧无效，日志流已损坏，断开并重连`))
    this.restartAfterStreamError(source, generation)
  }

  private emitLog(payload: StreamPayload): void {
    const entry = toStreamLogEntry(payload, this.options.runId)
    if (entry) this.options.onMessage?.(entry)
  }

  private emitStatus(payload: StreamPayload): void {
    const update = toStatusUpdate(payload)
    if (update) this.options.onStatusUpdate?.(update)
  }

  private handleStreamError(source: EventSource, generation: number, payload: StreamPayload): void {
    this.options.onError?.(payload.message ? String(payload.message) : '日志流服务端错误')
    const code = String(payload.code || '')
    if (code === 'cursor_expired') {
      this.options.onHistoricalLogsUpdate?.({ phase: 'gap' })
      this.lastEventId = ''
      this.restartAfterStreamError(source, generation)
      return
    }
    if (code === 'recovery_overflow' || code === 'recovery_unavailable') {
      // 两者服务端都会随即关流；主动关闭并计入熔断计数，避免依赖服务端行为。
      this.restartAfterStreamError(source, generation)
      return
    }
    if (code === 'session_revoked' || code === 'access_revoked') {
      this.closeIfActive(source, generation)
      this.closed = true
      this.options.onStateChange?.('failed')
      // P1-round6 5.4: SSE 收到 session_revoked 说明后端已撤销当前会话
      // (改密码 / 管理员 revoke / 长期未活跃)。仅当前标签的 EventSource
      // 感知不够, 其他标签仍持旧 access token 会继续 401 循环。
      // 广播 logout 让所有 tab 一起清 state + 跳登录。
      try {
        broadcastAuthEvent('logout')
      } catch {
        // BroadcastChannel 不可用不阻塞主流程
      }
    }
  }

  private restartAfterStreamError(source: EventSource, generation: number): void {
    this.closeIfActive(source, generation)
    if (this.closed) return
    this.options.onStateChange?.('disconnected')
    this.scheduleReconnect()
  }

  private handleSourceError(source: EventSource, generation: number): void {
    if (!this.isActive(source, generation)) return
    this.closeIfActive(source, generation)
    if (this.closed) return
    this.options.onStateChange?.('disconnected')
    this.scheduleReconnect()
  }

  private scheduleReconnect(): void {
    if (this.closed || this.reconnectTimer) return
    if (this.reconnectAttempts >= MAX_RECONNECT_ATTEMPTS) {
      this.options.onStateChange?.('failed')
      this.options.onError?.('日志流连接失败：已达最大重连次数，请点击“重连”手动恢复')
      return
    }
    this.reconnectAttempts += 1
    this.options.onStateChange?.('reconnecting')
    this.reconnectTimer = setTimeout(() => {
      this.reconnectTimer = null
      void this.connect()
    }, reconnectDelayMs(this.reconnectAttempts))
  }

  private startWatchdog(source: EventSource, generation: number): void {
    this.stopWatchdog()
    this.watchdog = setInterval(() => {
      if (!this.isActive(source, generation)) return
      if (Date.now() - this.lastEventAt <= WATCHDOG_STALE_MS) return
      Logger.warn('日志流超过 45s 无事件，判定为死连接，重连')
      this.handleSourceError(source, generation)
    }, WATCHDOG_CHECK_MS)
  }

  private parseData(event: MessageEvent): StreamPayload | null {
    try {
      return parseStreamPayload(event.data)
    } catch (error) {
      // 错误由 handleCorruptFrame 统一显式上报，这里只留诊断日志避免重复通知。
      Logger.error('解析日志 SSE 消息失败:', error)
      return null
    }
  }

  private touch(): void {
    this.lastEventAt = Date.now()
  }

  private recordLastEventId(event: MessageEvent): void {
    if (event.lastEventId) this.lastEventId = event.lastEventId
  }

  private markRealtimeHealthy(): void {
    if (this.historyComplete) this.markConnectionHealthy()
  }

  private markConnectionHealthy(): void {
    this.reconnectAttempts = 0
  }

  private isCurrent(generation: number): boolean {
    return !this.closed && generation === this.generation
  }

  private isActive(source: EventSource, generation: number): boolean {
    return this.isCurrent(generation) && source === this.source
  }

  private closeIfActive(source: EventSource, generation: number): void {
    if (!this.isActive(source, generation)) return
    source.close()
    this.source = null
    this.generation += 1
    this.stopWatchdog()
  }

  private closeSource(): void {
    this.source?.close()
    this.source = null
    this.stopWatchdog()
  }

  private stopWatchdog(): void {
    if (this.watchdog) clearInterval(this.watchdog)
    this.watchdog = null
  }

  private clearReconnectTimer(): void {
    if (this.reconnectTimer) clearTimeout(this.reconnectTimer)
    this.reconnectTimer = null
  }
}

export const createLogStreamConnection = (
  getTicket: () => Promise<string>,
  options: LogStreamOptions,
): LogStreamConnection => {
  const connection = new EventSourceLogConnection(getTicket, options)
  connection.start()
  return connection
}
