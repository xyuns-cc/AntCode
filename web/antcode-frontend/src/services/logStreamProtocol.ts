export type StreamPayload = Record<string, unknown>

/**
 * 后端已知的日志类型集合（P1-SSE-01）。
 * log_type 采用容错读：协议外的字符串值（如坏帧渗漏的 "unspecified"）不视为
 * 帧损坏——断流会让 cursor 无法推进、单 run 永久恢复失败；由渲染层
 * （logStreamMessages）降级为带原始类型标记的 system 行，cursor 照常推进。
 * 非字符串的 log_type 仍视为帧损坏（结构性错误，显式失败）。
 */
export const KNOWN_LOG_TYPES: ReadonlySet<string> = new Set(['stdout', 'stderr', 'system', 'application'])
/** 与后端 SSE 合同一致的枚举，未知值视为帧损坏（显式失败，不静默降级）。 */
const VALID_LOG_LEVELS: ReadonlySet<string> = new Set(['DEBUG', 'INFO', 'WARNING', 'ERROR', 'CRITICAL'])

export const isRecord = (value: unknown): value is StreamPayload => {
  return typeof value === 'object' && value !== null && !Array.isArray(value)
}

export const isPermanentTicketError = (error: unknown, statuses: ReadonlySet<number>): boolean => {
  if (!isRecord(error) || !isRecord(error.response)) return false
  const status = error.response.status
  return typeof status === 'number' && statuses.has(status)
}

export const parseStreamPayload = (value: string): StreamPayload => {
  const parsed: unknown = JSON.parse(value)
  if (!isRecord(parsed)) throw new Error('SSE 消息必须是 JSON 对象')
  return parsed
}

export const isValidStreamPayload = (type: string, payload: StreamPayload): boolean => {
  if (type === 'log_line') return validLogLine(payload)
  if (type === 'run_status') return validRunStatus(payload)
  if (type === 'historical_logs_end') return validHistoryEnd(payload)
  if (type === 'no_historical_logs') return validNoHistory(payload)
  if (type === 'recovery_complete') return nonNegativeInteger(payload.recovered_lines)
  if (type === 'stream_error') return typeof payload.code === 'string' && typeof payload.message === 'string'
  // stream_cursor 的游标在 SSE lastEventId 中，是不透明字符串，数据体不做结构断言。
  return type === 'historical_logs_start' || type === 'ping' || type === 'stream_cursor'
}

const validLogLine = (payload: StreamPayload): boolean => {
  if (!isRecord(payload.data)) return false
  const data = payload.data
  const content = data.content ?? data.message
  return typeof content === 'string'
    && optionalString(data.log_type)
    && optionalMember(data.level, VALID_LOG_LEVELS)
    && (data.sequence === undefined || data.sequence === null || typeof data.sequence === 'number')
    && (data.storage_id === undefined || data.storage_id === 0 || positiveInteger(data.storage_id))
}

const validRunStatus = (payload: StreamPayload): boolean => {
  return isRecord(payload.data) && typeof payload.data.status === 'string'
}

const validHistoryEnd = (payload: StreamPayload): boolean => {
  return nonNegativeInteger(payload.sent_lines) && optionalBoolean(payload.truncated)
}

/** no_historical_logs 语义上就是 0 行，sent_lines 允许缺省。 */
const validNoHistory = (payload: StreamPayload): boolean => {
  return (payload.sent_lines === undefined || nonNegativeInteger(payload.sent_lines))
    && optionalBoolean(payload.truncated)
}

const optionalMember = (value: unknown, members: ReadonlySet<string>): boolean => {
  if (value === undefined || value === null) return true
  return typeof value === 'string' && members.has(value)
}

const optionalString = (value: unknown): boolean => {
  return value === undefined || value === null || typeof value === 'string'
}

const optionalBoolean = (value: unknown): boolean => {
  return value === undefined || typeof value === 'boolean'
}

const nonNegativeInteger = (value: unknown): boolean => {
  return typeof value === 'number' && Number.isSafeInteger(value) && value >= 0
}

const positiveInteger = (value: unknown): boolean => {
  return typeof value === 'number' && Number.isSafeInteger(value) && value > 0
}
