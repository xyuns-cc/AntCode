import type { FC, RefObject } from 'react'
import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { Card, theme } from 'antd'
import styles from './LogViewer.module.css'
import VirtualizedLogList from './VirtualizedLogList'
import {
  VirtualLogViewerActions,
  VirtualLogViewerFooter,
  VirtualLogViewerSearch,
  VirtualLogViewerTitle,
} from './VirtualLogViewerControls'
import { createLogFilter } from './logFilterUtils'
import type { LogFilter } from './logSearchTypes'
import type { LogMessage, ViewerTheme } from './enhancedLogViewerTypes'
import type { SearchFilterResult, VirtualizedListRef, VirtualLogViewerProps } from './virtualLogTypes'
import { basicSearchLogs, calculateVirtualStats, displayOptionsFromFilter } from './virtualLogUtils'

const createFilterResult = (messages: LogMessage[], filter: LogFilter = createLogFilter()): SearchFilterResult => ({
  filteredMessages: messages,
  filter,
  stats: calculateVirtualStats(messages, { filteredCount: messages.length, total: messages.length }),
})

const useAdvancedFilter = (messages: LogMessage[]) => {
  const [result, setResult] = useState<SearchFilterResult>(() => createFilterResult(messages))
  const handleChange = useCallback((filteredMessages: LogMessage[], filter: LogFilter) => {
    setResult({
      filteredMessages,
      filter,
      stats: calculateVirtualStats(filteredMessages, { filteredCount: filteredMessages.length, total: messages.length }),
    })
  }, [messages.length])
  return { handleChange, result, setResult }
}

interface ModeResetOptions {
  filterMode: string
  listRef: RefObject<VirtualizedListRef>
  messages: LogMessage[]
  resetResult: (result: SearchFilterResult) => void
  setSearchText: (value: string) => void
}

const useFilterModeReset = (options: ModeResetOptions) => {
  const { filterMode, listRef, messages, resetResult, setSearchText } = options
  const previousMode = useRef(filterMode)
  useEffect(() => {
    if (previousMode.current === filterMode) return
    previousMode.current = filterMode
    setSearchText('')
    resetResult(createFilterResult(messages))
    listRef.current?.resetVirtualization()
  }, [filterMode, listRef, messages, resetResult, setSearchText])
}

interface LayoutProps {
  advanced: ReturnType<typeof useAdvancedFilter>
  config: Required<Pick<VirtualLogViewerProps,
    'enableAdvancedFilter' | 'estimatedItemHeight' | 'filterMode' | 'height' | 'searchable'
  >> & VirtualLogViewerProps
  filteredMessages: LogMessage[]
  displayOptions: ReturnType<typeof displayOptionsFromFilter>
  listRef: RefObject<VirtualizedListRef>
  searchText: string
  setSearchText: (value: string) => void
  stats: SearchFilterResult['stats']
  token: ViewerTheme
}

const ViewerLayout: FC<LayoutProps> = (props) => <Card
  title={<VirtualLogViewerTitle showFiltered={Boolean(props.searchText)} stats={props.stats} />}
  extra={<VirtualLogViewerActions listRef={props.listRef} onClear={props.config.onClear} />}
>
  {props.config.searchable && <VirtualLogViewerSearch
    advanced={props.config.enableAdvancedFilter} filterKey={props.config.filterMode} messages={props.config.messages}
    onAdvancedChange={props.advanced.handleChange} onSearchChange={props.setSearchText} searchText={props.searchText}
  />}
  <div className={styles.logContainer} style={{ height: props.config.height, overflow: 'hidden' }}>
    {props.filteredMessages.length === 0
      ? <div className={styles.emptyState}>
        {props.config.messages.length === 0 ? '暂无日志消息' : '没有匹配的日志消息'}
      </div>
      : <VirtualizedLogList
        ref={props.listRef}
        autoScroll={props.config.autoScroll}
        displayOptions={props.displayOptions}
        estimatedItemHeight={props.config.estimatedItemHeight}
        height={props.config.height}
        items={props.filteredMessages}
        onAutoScrollChange={props.config.onAutoScrollChange}
        onMessageClick={props.config.onMessageClick}
        searchText={props.searchText}
      />}
  </div>
  <VirtualLogViewerFooter searchText={props.searchText} stats={props.stats} token={props.token} />
</Card>

const VirtualLogViewer: FC<VirtualLogViewerProps> = (input) => {
  const config = {
    height: 400, estimatedItemHeight: 28, searchable: true, enableAdvancedFilter: false,
    filterMode: 'default', ...input,
  }
  const { token } = theme.useToken()
  const [searchText, setSearchText] = useState('')
  const listRef = useRef<VirtualizedListRef>(null)
  const advanced = useAdvancedFilter(config.messages)
  const filteredMessages = useMemo(() => (
    config.enableAdvancedFilter ? advanced.result.filteredMessages : basicSearchLogs(config.messages, searchText)
  ), [advanced.result.filteredMessages, config.enableAdvancedFilter, config.messages, searchText])
  const stats = useMemo(() => (
    config.enableAdvancedFilter
      ? advanced.result.stats
      : calculateVirtualStats(config.messages, { filteredCount: filteredMessages.length, total: config.messages.length })
  ), [advanced.result.stats, config.enableAdvancedFilter, config.messages, filteredMessages.length])
  const displayOptions = useMemo(() => displayOptionsFromFilter(
    config.enableAdvancedFilter ? advanced.result.filter : undefined,
  ), [advanced.result.filter, config.enableAdvancedFilter])
  useFilterModeReset({
    filterMode: config.filterMode, listRef, messages: config.messages,
    resetResult: advanced.setResult, setSearchText,
  })
  return <ViewerLayout
    advanced={advanced} config={config} displayOptions={displayOptions} filteredMessages={filteredMessages} listRef={listRef}
    searchText={searchText} setSearchText={setSearchText} stats={stats} token={token}
  />
}

export default VirtualLogViewer
