import { logService } from '@/services/logs'
import type { LogEntry, StructuredLogData } from '@/services/logs'

const DEFAULT_EXPORT_ENTRY_LIMIT = 1000
const LOG_PAGE_SIZE_LIMIT = 32

function normalizeEntryLimit(maxLines?: number): number {
  const limit = maxLines ?? DEFAULT_EXPORT_ENTRY_LIMIT
  if (!Number.isSafeInteger(limit) || limit <= 0) {
    throw new Error('maxLines 必须是正安全整数')
  }
  return limit
}

function validatePage(
  data: StructuredLogData,
  page: number,
  size: number,
  expectedTotal?: number
): void {
  if (data.page !== page || data.size !== size) {
    throw new Error(
      `日志分页响应不一致: expected=${page}/${size}, actual=${data.page}/${data.size}`
    )
  }
  if (!Number.isSafeInteger(data.total) || data.total < 0 || data.items.length > size) {
    throw new Error('日志分页响应包含非法计数')
  }
  if (expectedTotal !== undefined && data.total !== expectedTotal) {
    throw new Error(`日志分页期间总数发生变化: expected=${expectedTotal}, actual=${data.total}`)
  }
}

export async function fetchLogEntriesForExport(
  runId: string,
  maxLines?: number
): Promise<LogEntry[]> {
  const limit = normalizeEntryLimit(maxLines)
  const size = Math.min(limit, LOG_PAGE_SIZE_LIMIT)
  const entries: LogEntry[] = []
  const seenIds = new Set<string>()
  let total: number | undefined

  for (let page = 1; entries.length < limit; page += 1) {
    const response = await logService.getRunLogs(runId, { page, size })
    if (!response.success) throw new Error(response.message || '日志分页请求失败')
    validatePage(response.data, page, size, total)
    total ??= response.data.total
    const remaining = limit - entries.length
    const nextItems = response.data.items.slice(0, remaining)
    for (const entry of nextItems) {
      if (entry.id === undefined) throw new Error('日志分页响应缺少条目 ID')
      const id = String(entry.id)
      if (seenIds.has(id)) throw new Error(`日志分页响应包含重复条目: id=${id}`)
      seenIds.add(id)
    }
    entries.push(...nextItems)
    if (entries.length >= Math.min(total, limit)) return entries
    if (response.data.items.length === 0) {
      throw new Error(`日志分页提前结束: page=${page}, total=${response.data.total}`)
    }
  }
  return entries
}
