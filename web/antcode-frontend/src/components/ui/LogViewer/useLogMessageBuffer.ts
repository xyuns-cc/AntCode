import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import type { MutableRefObject } from 'react'
import type { LogMessage } from './enhancedLogViewerTypes'

const SEQUENCE_DEDUP_WINDOW_MULTIPLIER = 2
// P1-round6 5.3: 前端只按 maxLines 裁剪, 单条 1 MiB * 5000 行 = 5 GiB 内存。
// 每条消息强截 16 KiB, 保留原字节数提示; 与后端 sanitize 独立, 是终点防线。
const MAX_MESSAGE_CONTENT_BYTES = 16 * 1024
const MAX_BUFFER_CONTENT_BYTES = 16 * 1024 * 1024
const textDecoder = new TextDecoder()
const textEncoder = new TextEncoder()

const contentBytes = (content: string): number => textEncoder.encode(content).byteLength

export const truncateLogContent = (content: string, maxBytes = MAX_MESSAGE_CONTENT_BYTES): string => {
  const encoded = textEncoder.encode(content)
  if (encoded.byteLength <= maxBytes) return content
  const suffix = `\n… (前端截断, 原长度 ${encoded.byteLength} 字节)`
  const prefixLimit = Math.max(maxBytes - contentBytes(suffix), 0)
  let end = prefixLimit
  while (end > 0 && (encoded[end] & 0xc0) === 0x80) end -= 1
  return `${textDecoder.decode(encoded.subarray(0, end))}${suffix}`
}

const trimMessages = (messages: LogMessage[], maxLines: number): LogMessage[] => {
  let bytes = 0
  let start = messages.length
  while (start > 0 && messages.length - start < maxLines) {
    const nextBytes = contentBytes(messages[start - 1].content)
    if (bytes + nextBytes > MAX_BUFFER_CONTENT_BYTES) break
    bytes += nextBytes
    start -= 1
  }
  return messages.slice(start)
}
type Ref<T> = MutableRefObject<T>

interface BufferRefs {
  flushFrame: Ref<number | null>
  mounted: Ref<boolean>
  paused: Ref<boolean>
  pendingMessages: Ref<LogMessage[]>
  pendingContentBytes: Ref<number>
  pendingResync: Ref<boolean>
  pendingTrimmed: Ref<boolean>
  seenSequenceOrder: Ref<string[]>
  seenSequences: Ref<Set<string>>
}

const useBufferRefs = (): BufferRefs => {
  const flushFrame = useRef<number | null>(null)
  const mounted = useRef(true)
  const paused = useRef(false)
  const pendingMessages = useRef<LogMessage[]>([])
  const pendingContentBytes = useRef(0)
  const pendingResync = useRef(false)
  const pendingTrimmed = useRef(false)
  const seenSequenceOrder = useRef<string[]>([])
  const seenSequences = useRef(new Set<string>())
  return useMemo(() => ({
    flushFrame,
    mounted,
    paused,
    pendingMessages,
    pendingContentBytes,
    pendingResync,
    pendingTrimmed,
    seenSequenceOrder,
    seenSequences,
  }), [])
}

// 后端 sequence 从 0 起算（sequence=0 是合法值），只有真正没有 sequence 的消息才跳过去重。
const useSequenceDeduper = (refs: BufferRefs, maxLines: number) => useCallback((message: LogMessage) => {
  if (message.sequence === undefined) return true
  const key = message.sequenceIdentity ?? `${message.type}:${message.sequence}`
  if (refs.seenSequences.current.has(key)) return false
  refs.seenSequences.current.add(key)
  refs.seenSequenceOrder.current.push(key)
  const limit = maxLines * SEQUENCE_DEDUP_WINDOW_MULTIPLIER
  const expired = refs.seenSequenceOrder.current.length > limit
    ? refs.seenSequenceOrder.current.shift()
    : undefined
  if (expired) refs.seenSequences.current.delete(expired)
  return true
}, [maxLines, refs])

const useMessageQueue = (refs: BufferRefs, maxLines: number) => {
  const [messages, setMessages] = useState<LogMessage[]>([])
  const [pendingOverflowCount, setPendingOverflowCount] = useState(0)
  const rememberSequence = useSequenceDeduper(refs, maxLines)
  const flush = useCallback(() => {
    refs.flushFrame.current = null
    refs.pendingTrimmed.current = false
    const pending = refs.pendingMessages.current
    if (!refs.mounted.current || pending.length === 0) return
    refs.pendingMessages.current = []
    refs.pendingContentBytes.current = 0
    setMessages((previous) => trimMessages([...previous, ...pending], maxLines))
  }, [maxLines, refs])
  // 待刷队列按环形窗口裁剪最旧行：后台标签页 RAF 不执行时内存有界，且不断连、
  // 不丢 cursor，不会触发全量历史重连风暴；裁剪事件通过计数器暴露给 UI 显式提示。
  const trimPendingToWindow = useCallback(() => {
    const limit = Math.max(maxLines, 1)
    let trimCount = Math.max(refs.pendingMessages.current.length - limit, 0)
    for (let index = 0; index < trimCount; index += 1) {
      refs.pendingContentBytes.current -= contentBytes(refs.pendingMessages.current[index].content)
    }
    while (trimCount < refs.pendingMessages.current.length
      && refs.pendingContentBytes.current > MAX_BUFFER_CONTENT_BYTES) {
      refs.pendingContentBytes.current -= contentBytes(refs.pendingMessages.current[trimCount].content)
      trimCount += 1
    }
    if (trimCount <= 0) return
    refs.pendingMessages.current.splice(0, trimCount)
    if (refs.pendingTrimmed.current) return
    refs.pendingTrimmed.current = true
    setPendingOverflowCount((value) => value + 1)
  }, [maxLines, refs])
  const enqueue = useCallback((message: LogMessage, markResync: boolean) => {
    if (!refs.mounted.current) return
    if (refs.paused.current) {
      if (markResync) refs.pendingResync.current = true
      return
    }
    if (!rememberSequence(message)) return
    // P1-round6 5.3: 入队前按字节截断 content, 防内存暴涨。
    const bounded = message.content === undefined || message.content === null
      ? message
      : { ...message, content: truncateLogContent(message.content) }
    refs.pendingMessages.current.push(bounded)
    refs.pendingContentBytes.current += contentBytes(bounded.content)
    trimPendingToWindow()
    if (refs.flushFrame.current === null) refs.flushFrame.current = requestAnimationFrame(flush)
  }, [flush, refs, rememberSequence, trimPendingToWindow])
  const resetMessages = useCallback(() => {
    refs.pendingMessages.current = []
    refs.pendingContentBytes.current = 0
    refs.pendingTrimmed.current = false
    refs.seenSequences.current.clear()
    refs.seenSequenceOrder.current = []
    if (refs.flushFrame.current !== null) cancelAnimationFrame(refs.flushFrame.current)
    refs.flushFrame.current = null
    setMessages([])
  }, [refs])
  useEffect(() => {
    refs.mounted.current = true
    return () => {
      refs.mounted.current = false
      if (refs.flushFrame.current !== null) cancelAnimationFrame(refs.flushFrame.current)
    }
  }, [refs])
  return { enqueue, messages, pendingOverflowCount, resetMessages }
}

const usePauseState = (refs: BufferRefs) => {
  const [isPaused, setIsPaused] = useState(false)
  const setPaused = useCallback((paused: boolean) => {
    refs.paused.current = paused
    setIsPaused(paused)
  }, [refs])
  const consumePendingResync = useCallback(() => {
    const pending = refs.pendingResync.current
    refs.pendingResync.current = false
    return pending
  }, [refs])
  const discardPendingResync = useCallback(() => {
    refs.pendingResync.current = false
  }, [refs])
  return { consumePendingResync, discardPendingResync, isPaused, setPaused }
}

export const useLogMessageBuffer = ({ maxLines }: { maxLines: number }) => {
  const refs = useBufferRefs()
  const queue = useMessageQueue(refs, maxLines)
  const pause = usePauseState(refs)
  const { enqueue, resetMessages } = queue
  const addStreamMessage = useCallback((item: LogMessage) => enqueue(item, true), [enqueue])
  const addNotice = useCallback((item: LogMessage) => enqueue(item, false), [enqueue])
  const beginHistoryReplay = useCallback(() => {
    if (refs.paused.current) {
      refs.pendingResync.current = true
      return false
    }
    resetMessages()
    return true
  }, [refs, resetMessages])
  const api = useMemo(() => ({
    addNotice,
    addStreamMessage,
    beginHistoryReplay,
    consumePendingResync: pause.consumePendingResync,
    discardPendingResync: pause.discardPendingResync,
  }), [addNotice, addStreamMessage, beginHistoryReplay, pause.consumePendingResync, pause.discardPendingResync])
  return {
    api,
    addNotice,
    isPaused: pause.isPaused,
    messages: queue.messages,
    pendingOverflowCount: queue.pendingOverflowCount,
    resetMessages,
    setPaused: pause.setPaused,
  }
}
