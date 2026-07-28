import type { LogEntry, LogLevel, LogType } from './logs'
import { isRecord, KNOWN_LOG_TYPES, type StreamPayload } from './logStreamProtocol'

export type StatusUpdate = { status: string; message?: string; progress?: number }

/**
 * P1-SSE-01: 协议外 log_type 的容错降级。
 * 未知类型不断流（断流会卡住 cursor，单 run 永久恢复失败），而是降级为
 * 带原始类型标记的 system 行；日志 id 保留原始类型字符串，避免与真实
 * system 序列（sequence 按 run_id + log_type 分配）冲突。
 */
const degradeUnknownLogType = (rawType: string, message: string): { logType: LogType; message: string } => {
  if (KNOWN_LOG_TYPES.has(rawType)) return { logType: rawType as LogType, message }
  return { logType: 'system', message: `[未知日志类型 ${rawType}] ${message}` }
}

export const toStreamLogEntry = (payload: StreamPayload, fallbackRunId: string): LogEntry | null => {
  if (!isRecord(payload.data)) return null
  const data = payload.data
  const sequence = typeof data.sequence === 'number' ? data.sequence : undefined
  const rawType = typeof data.log_type === 'string' && data.log_type ? data.log_type : 'stdout'
  const runId = String(data.run_id || fallbackRunId)
  const id = sequence === undefined
    ? `${Date.now()}_${Math.random().toString(36).slice(2, 10)}`
    : `${runId}:${rawType}:${sequence}`
  const { logType, message } = degradeUnknownLogType(rawType, String(data.content ?? data.message ?? ''))
  return {
    id,
    timestamp: String(data.timestamp || payload.timestamp || new Date().toISOString()),
    level: (data.level || 'INFO') as LogLevel,
    log_type: logType,
    run_id: runId,
    message,
    source: data.source ? String(data.source) : undefined,
    sequence,
  }
}

export const toStatusUpdate = (payload: StreamPayload): StatusUpdate | null => {
  if (!isRecord(payload.data) || !payload.data.status) return null
  return {
    status: String(payload.data.status),
    message: payload.data.message ? String(payload.data.message) : undefined,
    progress: typeof payload.data.progress === 'number' ? payload.data.progress : undefined,
  }
}
