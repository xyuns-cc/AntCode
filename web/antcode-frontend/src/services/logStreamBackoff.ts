/** SSE 日志流重连退避策略：指数退避 + 上限 + 随机抖动（防止批量客户端同步风暴）。 */
export const MAX_RECONNECT_ATTEMPTS = 5

const RECONNECT_BASE_DELAY_MS = 1000
const RECONNECT_BACKOFF_FACTOR = 2
const MAX_RECONNECT_DELAY_MS = 30_000
const RECONNECT_JITTER_RATIO = 0.1

export const reconnectDelayMs = (attempt: number): number => {
  const exponential = RECONNECT_BASE_DELAY_MS * Math.pow(RECONNECT_BACKOFF_FACTOR, attempt - 1)
  const capped = Math.min(exponential, MAX_RECONNECT_DELAY_MS)
  return capped + capped * RECONNECT_JITTER_RATIO * Math.random()
}
