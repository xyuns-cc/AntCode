import type { FC, RefObject } from 'react'
import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { message, theme } from 'antd'
import EnhancedLogViewerContent, { LogViewerFooter } from './EnhancedLogViewerContent'
import EnhancedLogViewerFilters from './EnhancedLogViewerFilters'
import EnhancedLogViewerHeader from './EnhancedLogViewerHeader'
import EnhancedLogViewerStats from './EnhancedLogViewerStats'
import styles from './LogViewer.module.css'
import {
  DEFAULT_LEVELS,
  DEFAULT_TYPES,
  type EnhancedLogViewerProps,
  type ExportFormat,
  type FilterState,
  type LogMessage,
  type LogStats,
  type ViewerTheme,
} from './enhancedLogViewerTypes'
import { calculateLogStats, createNotice, downloadLogs, filterLogMessages } from './enhancedLogViewerUtils'
import { useLogMessageBuffer } from './useLogMessageBuffer'
import { useLogStreamController } from './useLogStreamController'

const DEFAULTS = {
  autoConnect: true, enableExport: true, enableSearch: true, enableVirtualization: false,
  height: 600, maxLines: 1000, showControls: true, showStderr: true, showStdout: true,
} as const

type ViewerBuffer = ReturnType<typeof useLogMessageBuffer>
type ViewerStream = ReturnType<typeof useLogStreamController>
type ViewerFilters = ReturnType<typeof useViewerFilters>
type ViewerConfig = Omit<EnhancedLogViewerProps, keyof typeof DEFAULTS> & {
  [Key in keyof typeof DEFAULTS]: NonNullable<EnhancedLogViewerProps[Key]>
}

const useViewerFilters = (messages: LogMessage[]) => {
  const [searchText, setSearchText] = useState('')
  const [selectedLevels, setSelectedLevels] = useState<string[]>([...DEFAULT_LEVELS])
  const [selectedTypes, setSelectedTypes] = useState<string[]>([...DEFAULT_TYPES])
  const filters = useMemo<FilterState>(() => ({ searchText, selectedLevels, selectedTypes }), [
    searchText, selectedLevels, selectedTypes,
  ])
  const displayedMessages = useMemo(() => filterLogMessages(messages, filters), [messages, filters])
  const resetFilters = useCallback(() => {
    setSearchText('')
    setSelectedLevels([...DEFAULT_LEVELS])
    setSelectedTypes([...DEFAULT_TYPES])
  }, [])
  return {
    displayedMessages, resetFilters, searchText, selectedLevels, selectedTypes,
    setSearchText, setSelectedLevels, setSelectedTypes,
  }
}

interface ActionOptions {
  buffer: ViewerBuffer
  displayedMessages: LogMessage[]
  isViewTruncated: boolean
  runId: string
  stream: ViewerStream
}

const useViewerActions = ({ buffer, displayedMessages, isViewTruncated, runId, stream }: ActionOptions) => {
  const [isAutoScroll, setIsAutoScroll] = useState(true)
  const [isFullscreen, setIsFullscreen] = useState(false)
  const { addNotice, resetMessages, setPaused } = buffer
  const { resumeAfterPause } = stream
  const clearLogs = useCallback(() => {
    resetMessages()
    addNotice(createNotice('info', '[清除] 日志已清除'))
  }, [addNotice, resetMessages])
  const changePaused = useCallback((paused: boolean) => {
    setPaused(paused)
    if (!paused) resumeAfterPause()
  }, [resumeAfterPause, setPaused])
  const exportLogs = useCallback((format: ExportFormat) => {
    if (displayedMessages.length === 0) {
      message.warning('没有日志可导出')
      return
    }
    downloadLogs(displayedMessages, runId, format)
    if (isViewTruncated) {
      message.warning(`已导出为 ${format.toUpperCase()} 格式，但内容仅含浏览器缓冲的最新日志，完整日志请使用服务端日志下载`)
      return
    }
    message.success(`日志已导出为 ${format.toUpperCase()} 格式`)
  }, [displayedMessages, isViewTruncated, runId])
  const toggleFullscreen = useCallback(() => setIsFullscreen((current) => !current), [])
  return {
    changePaused, clearLogs, exportLogs, isAutoScroll, isFullscreen,
    setIsAutoScroll, toggleFullscreen,
  }
}

interface ViewerEffectOptions {
  containerRef: RefObject<HTMLDivElement>
  displayedMessages: LogMessage[]
  isAutoScroll: boolean
  isPaused: boolean
  messages: LogMessage[]
  onLogUpdate?: (logs: string[]) => void
}

const useViewerEffects = (options: ViewerEffectOptions) => {
  const { containerRef, displayedMessages, isAutoScroll, isPaused, messages, onLogUpdate } = options
  useEffect(() => {
    if (onLogUpdate && messages.length > 0) {
      onLogUpdate(messages.map((item) => item.content))
    }
  }, [messages, onLogUpdate])
  useEffect(() => {
    const container = containerRef.current
    if (!isAutoScroll || isPaused || !container) return
    container.scrollTop = container.scrollHeight
  }, [containerRef, displayedMessages, isAutoScroll, isPaused])
}

interface LayoutProps {
  actions: ReturnType<typeof useViewerActions>
  buffer: ViewerBuffer
  config: ViewerConfig
  containerRef: RefObject<HTMLDivElement>
  filters: ViewerFilters
  stats: LogStats
  stream: ViewerStream
  token: ViewerTheme
}

const ViewerLayout: FC<LayoutProps> = ({ actions, buffer, config, containerRef, filters, stats, stream, token }) => (
  <div className={`${styles.logViewer} ${actions.isFullscreen ? styles.fullscreen : ''}`}>
    <div style={{ padding: '20px 24px', background: token.colorBgContainer }}>
      <EnhancedLogViewerHeader
        {...stream} enableExport={config.enableExport} isFullscreen={actions.isFullscreen}
        onClear={actions.clearLogs} onConnect={stream.connect} onDisconnect={stream.disconnect}
        onExport={actions.exportLogs} onReconnect={stream.reconnect} onToggleFullscreen={actions.toggleFullscreen}
        runId={config.runId} showControls={config.showControls} token={token}
      />
      <EnhancedLogViewerStats
        filteredCount={filters.displayedMessages.length}
        stats={stats} token={token}
      />
      <EnhancedLogViewerFilters
        enableSearch={config.enableSearch} isAutoScroll={actions.isAutoScroll} isPaused={buffer.isPaused}
        onAutoScrollChange={actions.setIsAutoScroll} onLevelsChange={filters.setSelectedLevels}
        onPauseChange={actions.changePaused} onReset={filters.resetFilters} onSearchChange={filters.setSearchText}
        onTypesChange={filters.setSelectedTypes} searchText={filters.searchText}
        selectedLevels={filters.selectedLevels} selectedTypes={filters.selectedTypes} token={token}
      />
      <EnhancedLogViewerContent
        allMessageCount={buffer.messages.length} autoScroll={actions.isAutoScroll}
        connectionStatus={stream.connectionStatus} containerRef={containerRef}
        enableVirtualization={config.enableVirtualization} height={config.height} maxLines={config.maxLines}
        messages={filters.displayedMessages} onAutoScrollChange={actions.setIsAutoScroll}
        onClear={actions.clearLogs} searchText={filters.searchText} token={token}
      />
      <LogViewerFooter
        allMessageCount={buffer.messages.length} connectionStatus={stream.connectionStatus}
        maxLines={config.maxLines} messages={filters.displayedMessages} searchText={filters.searchText} token={token}
      />
    </div>
  </div>
)

const EnhancedLogViewer: FC<EnhancedLogViewerProps> = (props) => {
  const config = { ...DEFAULTS, ...props }
  const { token } = theme.useToken()
  const containerRef = useRef<HTMLDivElement>(null)
  const buffer = useLogMessageBuffer({ maxLines: config.maxLines })
  const stream = useLogStreamController({
    autoConnect: config.autoConnect, buffer: buffer.api, onStatusUpdate: config.onStatusUpdate,
    pendingOverflowCount: buffer.pendingOverflowCount, runId: config.runId,
    showStderr: config.showStderr, showStdout: config.showStdout,
  })
  const filters = useViewerFilters(buffer.messages)
  const stats = useMemo(() => calculateLogStats(buffer.messages), [buffer.messages])
  const isViewTruncated = buffer.messages.length >= config.maxLines || stream.history.truncated
  const actions = useViewerActions({
    buffer, displayedMessages: filters.displayedMessages, isViewTruncated, runId: config.runId, stream,
  })
  useViewerEffects({
    containerRef, displayedMessages: filters.displayedMessages, isAutoScroll: actions.isAutoScroll,
    isPaused: buffer.isPaused, messages: buffer.messages, onLogUpdate: config.onLogUpdate,
  })
  return <ViewerLayout
    actions={actions} buffer={buffer} config={config} containerRef={containerRef}
    filters={filters} stats={stats} stream={stream} token={token}
  />
}

export default EnhancedLogViewer
