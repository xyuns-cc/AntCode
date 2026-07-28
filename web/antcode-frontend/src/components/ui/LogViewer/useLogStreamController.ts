import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import type { Dispatch, MutableRefObject, SetStateAction } from 'react'
import { logService, type HistoricalLogsUpdate, type LogStreamConnection, type LogStreamOptions } from '@/services/logs'
import Logger from '@/utils/logger'
import type { ConnectionStatus, ExecutionStatusUpdate, HistoricalStatus, LogMessage } from './enhancedLogViewerTypes'
import { createNotice, isVisibleLogType, toLogMessage } from './enhancedLogViewerUtils'

const AUTO_CONNECT_DELAY_MS = 500
const MANUAL_RECONNECT_DELAY_MS = 1000
type Ref<T> = MutableRefObject<T>

interface MessageBufferApi {
  addNotice: (message: LogMessage) => void
  addStreamMessage: (message: LogMessage) => void
  beginHistoryReplay: () => boolean
  consumePendingResync: () => boolean
  discardPendingResync: () => void
}

interface ControllerOptions {
  autoConnect: boolean
  buffer: MessageBufferApi
  onStatusUpdate?: (status: ExecutionStatusUpdate) => void
  pendingOverflowCount: number
  runId: string
  showStderr: boolean
  showStdout: boolean
}

interface HistoryState {
  sentLines: number
  status: HistoricalStatus
  truncated: boolean
}

interface ControllerRuntime {
  connection: Ref<LogStreamConnection | null>
  connectionStatusRef: Ref<ConnectionStatus>
  gapDetected: Ref<boolean>
  manualDisconnect: Ref<boolean>
  mounted: Ref<boolean>
  reconnectTimer: Ref<ReturnType<typeof setTimeout> | null>
  setHasConnection: Dispatch<SetStateAction<boolean>>
  setHistory: Dispatch<SetStateAction<HistoryState>>
  stopConnection: () => void
  terminalFailure: Ref<boolean>
  updateStatus: (status: ConnectionStatus) => void
}

interface RuntimeState {
  connectionStatus: ConnectionStatus
  hasConnection: boolean
  history: HistoryState
  runtime: ControllerRuntime
}

const INITIAL_HISTORY: HistoryState = { sentLines: 0, status: 'idle', truncated: false }

const useControllerRuntime = (): RuntimeState => {
  const [connectionStatus, setConnectionStatus] = useState<ConnectionStatus>('disconnected')
  const [history, setHistory] = useState<HistoryState>(INITIAL_HISTORY)
  const [hasConnection, setHasConnection] = useState(false)
  const mounted = useRef(true)
  const connection = useRef<LogStreamConnection | null>(null)
  const connectionStatusRef = useRef<ConnectionStatus>('disconnected')
  const gapDetected = useRef(false)
  const reconnectTimer = useRef<ReturnType<typeof setTimeout> | null>(null)
  const manualDisconnect = useRef(false)
  const terminalFailure = useRef(false)
  const updateStatus = useCallback((status: ConnectionStatus) => {
    connectionStatusRef.current = status
    if (mounted.current) setConnectionStatus(status)
  }, [])
  const stopConnection = useCallback(() => {
    connection.current?.disconnect()
    connection.current = null
    if (mounted.current) setHasConnection(false)
  }, [])
  const runtime = useMemo(() => ({
    connection, connectionStatusRef, gapDetected, manualDisconnect, mounted, reconnectTimer, setHasConnection,
    setHistory, stopConnection, terminalFailure, updateStatus,
  }), [stopConnection, updateStatus])
  return { connectionStatus, hasConnection, history, runtime }
}

const useHandleLog = (options: ControllerOptions) => useCallback((entry: Parameters<typeof toLogMessage>[0]) => {
  const message = toLogMessage(entry)
  const visibility = { showStdout: options.showStdout, showStderr: options.showStderr }
  if (isVisibleLogType(message.type, visibility)) options.buffer.addStreamMessage(message)
}, [options.buffer, options.showStderr, options.showStdout])

const useHandleError = (buffer: MessageBufferApi, runtime: ControllerRuntime) => useCallback((error: unknown) => {
  Logger.error('日志流连接错误:', error)
  runtime.updateStatus('error')
  const details = error instanceof Error ? error.message : String(error)
  buffer.addNotice(createNotice('error', `[错误] 日志流连接错误: ${details}`))
}, [buffer, runtime])

const useHandleState = (buffer: MessageBufferApi, runtime: ControllerRuntime) => useCallback((state: string) => {
  if (!runtime.mounted.current) return
  if (state === 'connected') {
    runtime.updateStatus('connected')
    buffer.addNotice(createNotice('success', '[成功] 日志流连接已建立'))
  } else if (state === 'reconnecting') {
    runtime.updateStatus('connecting')
    buffer.addNotice(createNotice('warning', '[重连] 日志流连接中...'))
  } else if (state === 'disconnected') {
    runtime.updateStatus('disconnected')
  } else if (state === 'failed' || state === 'error') {
    runtime.terminalFailure.current = state === 'failed'
    runtime.updateStatus('error')
  }
}, [buffer, runtime])

const useHandleStatus = (options: ControllerOptions) => {
  const { buffer, onStatusUpdate } = options
  return useCallback((status: ExecutionStatusUpdate) => {
    Logger.info('收到执行状态更新:', status)
    buffer.addStreamMessage(createNotice('info', `[状态] ${status.message || status.status}`))
    onStatusUpdate?.(status)
  }, [buffer, onStatusUpdate])
}

const useHandleHistory = (buffer: MessageBufferApi, runtime: ControllerRuntime) => useCallback((update: HistoricalLogsUpdate) => {
  if (!runtime.mounted.current) return
  if (update.phase === 'gap') {
    runtime.gapDetected.current = true
    return
  }
  if (update.phase === 'loading') {
    runtime.setHistory({ sentLines: 0, status: 'loading', truncated: false })
    if (buffer.beginHistoryReplay()) {
      if (runtime.gapDetected.current) {
        buffer.addNotice(createNotice('warning', '[缺口] 原日志断点已过期，已重新加载当前历史窗口'))
        runtime.gapDetected.current = false
      }
      buffer.addNotice(createNotice('info', '[历史] 正在加载历史日志...'))
    }
    return
  }
  const sentLines = update.sentLines ?? 0
  if (update.phase === 'empty') {
    const truncated = update.truncated === true
    runtime.setHistory({ sentLines: 0, status: 'empty', truncated })
    const content = truncated ? '[历史] 历史日志超过回放容量，未加载超限内容' : '[历史] 无历史日志，已进入实时模式'
    buffer.addNotice(createNotice(truncated ? 'warning' : 'info', content))
    return
  }
  const truncated = update.truncated === true
  runtime.setHistory({ sentLines, status: 'loaded', truncated })
  const content = truncated
    ? `[历史] 仅加载最新 ${sentLines} 行，较早日志已截断`
    : `[历史] 历史日志加载完成，共 ${sentLines} 行`
  buffer.addNotice(createNotice(truncated ? 'warning' : 'info', content))
}, [buffer, runtime])

const useStreamHandlers = (options: ControllerOptions, runtime: ControllerRuntime): LogStreamOptions => {
  const onMessage = useHandleLog(options)
  const onError = useHandleError(options.buffer, runtime)
  const onStateChange = useHandleState(options.buffer, runtime)
  const onStatusUpdate = useHandleStatus(options)
  const onHistoricalLogsUpdate = useHandleHistory(options.buffer, runtime)
  return useMemo(() => ({
    runId: options.runId, onMessage, onError, onStateChange, onStatusUpdate, onHistoricalLogsUpdate,
  }), [onError, onHistoricalLogsUpdate, onMessage, onStateChange, onStatusUpdate, options.runId])
}

const useConnect = (options: ControllerOptions, runtime: ControllerRuntime, handlers: LogStreamOptions) => useCallback(() => {
  const current = runtime.connectionStatusRef.current
  if (current === 'connected' || current === 'connecting') return
  runtime.manualDisconnect.current = false
  runtime.terminalFailure.current = false
  runtime.stopConnection()
  runtime.updateStatus('connecting')
  runtime.setHistory(INITIAL_HISTORY)
  options.buffer.addNotice(createNotice('info', `正在连接到运行ID: ${options.runId}`))
  try {
    const connection = logService.connectLogStream(handlers)
    if (!connection) throw new Error('无法创建日志流连接')
    runtime.connection.current = connection
    runtime.setHasConnection(true)
  } catch (error) {
    handlers.onError?.(error)
  }
}, [handlers, options.buffer, options.runId, runtime])

const useDisconnectActions = (buffer: MessageBufferApi, runtime: ControllerRuntime, connect: () => void) => {
  const disconnect = useCallback(() => {
    runtime.manualDisconnect.current = true
    if (runtime.reconnectTimer.current) clearTimeout(runtime.reconnectTimer.current)
    runtime.reconnectTimer.current = null
    runtime.stopConnection()
    runtime.terminalFailure.current = false
    runtime.gapDetected.current = false
    runtime.updateStatus('disconnected')
    runtime.setHistory(INITIAL_HISTORY)
    buffer.discardPendingResync()
    buffer.addNotice(createNotice('info', '[断开] 手动断开连接'))
  }, [buffer, runtime])
  const reconnect = useCallback(() => {
    disconnect()
    runtime.manualDisconnect.current = false
    runtime.reconnectTimer.current = setTimeout(() => {
      runtime.reconnectTimer.current = null
      connect()
    }, MANUAL_RECONNECT_DELAY_MS)
  }, [connect, disconnect, runtime])
  const resumeAfterPause = useCallback(() => {
    const needsResync = buffer.consumePendingResync()
    if (!needsResync || runtime.terminalFailure.current || runtime.manualDisconnect.current) return
    reconnect()
  }, [buffer, reconnect, runtime])
  return useMemo(() => ({ disconnect, reconnect, resumeAfterPause }), [disconnect, reconnect, resumeAfterPause])
}

const useControllerEffects = (
  options: ControllerOptions,
  state: RuntimeState,
  connect: () => void,
) => {
  const { runtime } = state
  const handledOverflow = useRef(0)
  useEffect(() => {
    runtime.manualDisconnect.current = false
    runtime.stopConnection()
    runtime.terminalFailure.current = false
    runtime.gapDetected.current = false
    runtime.updateStatus('disconnected')
    runtime.setHistory(INITIAL_HISTORY)
    return runtime.stopConnection
  }, [options.runId, runtime])
  useEffect(() => {
    if (!options.autoConnect || !options.runId || runtime.manualDisconnect.current) return undefined
    if (state.connectionStatus !== 'disconnected' || state.hasConnection) return undefined
    const timer = setTimeout(connect, AUTO_CONNECT_DELAY_MS)
    return () => clearTimeout(timer)
  }, [connect, options.autoConnect, options.runId, runtime, state.connectionStatus, state.hasConnection])
  useEffect(() => {
    runtime.mounted.current = true
    return () => {
      runtime.mounted.current = false
      if (runtime.reconnectTimer.current) clearTimeout(runtime.reconnectTimer.current)
      runtime.stopConnection()
    }
  }, [runtime])
  // 缓冲裁剪只提示不重连：连接与 cursor 保持不变，避免后台标签页的全量回放重连风暴。
  useEffect(() => {
    if (options.pendingOverflowCount <= handledOverflow.current) return
    handledOverflow.current = options.pendingOverflowCount
    options.buffer.addNotice(createNotice('warning', '[溢出] 日志产生过快，视图仅保留最新窗口，完整日志请使用导出功能'))
  }, [options.buffer, options.pendingOverflowCount])
}

export const useLogStreamController = (options: ControllerOptions) => {
  const state = useControllerRuntime()
  const handlers = useStreamHandlers(options, state.runtime)
  const connect = useConnect(options, state.runtime, handlers)
  const actions = useDisconnectActions(options.buffer, state.runtime, connect)
  useControllerEffects(options, state, connect)
  return {
    connect,
    connectionStatus: state.connectionStatus,
    disconnect: actions.disconnect,
    history: state.history,
    reconnect: actions.reconnect,
    resumeAfterPause: actions.resumeAfterPause,
  }
}
