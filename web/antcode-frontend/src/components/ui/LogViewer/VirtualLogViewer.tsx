import React, { useState, useEffect, useRef, useCallback, useMemo } from 'react'
import { Card, Input, Space, Tag, Button } from 'antd'
import { SearchOutlined, ClearOutlined } from '@ant-design/icons'
import styles from './LogViewer.module.css'
import LogSearchFilter from './LogSearchFilter'
import type { LogFilter } from './LogSearchFilter'

const { Search } = Input

// 日志消息接口
interface LogMessage {
  id: string
  type: 'stdout' | 'stderr' | 'info' | 'error' | 'warning' | 'success'
  content: string
  timestamp: string
  level?: string
  source?: string
}

// 过滤结果接口
interface SearchFilterResult {
  filteredMessages: LogMessage[]
  stats: {
    total: number
    filtered: number
    stdout: number
    stderr: number
    errors: number
    warnings: number
    info: number
    debug: number
  }
}

// 简化的虚拟化实现 - 支持动态行高
interface VirtualizedListProps {
  items: LogMessage[]
  height: number
  estimatedItemHeight: number
  searchText: string
  onMessageClick?: (message: LogMessage) => void
}

// 添加ref接口
interface VirtualizedListRef {
  scrollToBottom: () => void
  resetVirtualization: () => void
}

const VirtualizedList = React.forwardRef<VirtualizedListRef, VirtualizedListProps>(({
  items,
  height,
  estimatedItemHeight,
  searchText,
  onMessageClick
}, ref) => {
  const [scrollTop, setScrollTop] = useState(0)
  const [shouldAutoScroll, setShouldAutoScroll] = useState(true)
  const [isUserScrolling, setIsUserScrolling] = useState(false)
  const lastScrollTop = useRef(0)
  const containerRef = useRef<HTMLDivElement>(null)
  const itemHeights = useRef<Record<string, number>>({})
  const itemPositions = useRef<number[]>([])
  const [positionsVersion, setPositionsVersion] = useState(0)
  const [totalHeight, setTotalHeight] = useState(0)
  const lastItemsLength = useRef(items.length)

  // 计算每个项目的位置
  const calculateItemPositions = useCallback(() => {
    let position = 0
    const positions: number[] = []

    for (let i = 0; i < items.length; i++) {
      positions[i] = position
      const message = items[i]
      const height = itemHeights.current[message?.id ?? ''] || estimatedItemHeight
      position += height
    }

    const previousPositions = itemPositions.current
    let hasChanged = previousPositions.length !== positions.length

    if (!hasChanged) {
      for (let i = 0; i < positions.length; i++) {
        if (Math.abs((previousPositions[i] || 0) - positions[i]) > 0.5) {
          hasChanged = true
          break
        }
      }
    }

    itemPositions.current = positions

    if (hasChanged) {
      setPositionsVersion((prev) => prev + 1)
    }

    setTotalHeight((prev) => {
      if (Math.abs(prev - position) > 0.5 || hasChanged) {
        return position
      }
      return prev
    })
  }, [items, estimatedItemHeight])
  
  // 更新项目高度 - 添加防抖避免频繁重新计算
  const updateItemHeight = useCallback((id: string, height: number) => {
    if (!id) return
    const currentHeight = itemHeights.current[id]

    // 只有高度变化超过1px时才更新，避免微小差异导致频繁重计算
    if (Math.abs((currentHeight || 0) - height) > 1) {
      itemHeights.current[id] = height

      // 使用requestAnimationFrame避免在滚动过程中频繁更新布局
      requestAnimationFrame(() => {
        calculateItemPositions()
      })
    }
  }, [calculateItemPositions])
  
  // 计算可见范围
  const getVisibleRange = useCallback(() => {
    const positions = itemPositions.current
    let start = 0
    let end = items.length - 1

    // 找到第一个可见项
    for (let i = 0; i < positions.length; i++) {
      const message = items[i]
      const height = itemHeights.current[message?.id ?? ''] || estimatedItemHeight
      if (positions[i] + height >= scrollTop) {
        start = Math.max(0, i - 1) // 提前渲染一项
        break
      }
    }

    // 找到最后一个可见项
    for (let i = start; i < positions.length; i++) {
      const message = items[i]
      const itemHeight = itemHeights.current[message?.id ?? ''] || estimatedItemHeight
      if (positions[i] + itemHeight >= scrollTop + height) {
        end = Math.min(items.length - 1, i + 1) // 延后渲染一项
        break
      }
    }
    
    return { start, end }
  }, [items, scrollTop, height, estimatedItemHeight, positionsVersion])

  const { start: visibleStart, end: visibleEnd } = getVisibleRange()
  const visibleItems = items.slice(visibleStart, visibleEnd + 1)

  // 处理滚动
  const handleScroll = (e: React.UIEvent<HTMLDivElement>) => {
    const newScrollTop = e.currentTarget.scrollTop
    setScrollTop(newScrollTop)

    // 检查是否在底部附近（距离底部30px以内才算真正的底部）
    const container = e.currentTarget
    const distanceToBottom = container.scrollHeight - newScrollTop - container.clientHeight
    const isAtBottom = distanceToBottom < 30

    const isScrollingUp = newScrollTop < lastScrollTop.current

    // 用户向上滚动且未到达底部时，禁用自动滚动
    if (isScrollingUp && !isAtBottom) {
      setIsUserScrolling(true)
      setShouldAutoScroll(false)
    }
    // 用户滚动到真正的底部时，重新启用自动滚动
    else if (isAtBottom) {
      setIsUserScrolling(false)
      setShouldAutoScroll(true)
    }

    lastScrollTop.current = newScrollTop
  }

  // 初始化位置计算
  useEffect(() => {
    calculateItemPositions()
  }, [calculateItemPositions])

  // 当items内容变化时重置虚拟化状态
  useEffect(() => {
    const currentIds = new Set(items.map((item) => item.id))
    Object.keys(itemHeights.current).forEach((id) => {
      if (!currentIds.has(id)) {
        delete itemHeights.current[id]
      }
    })

    // 检查是否需要完全重置
    const shouldFullReset =
      items.length === 0 ||
      lastItemsLength.current === 0 ||
      Math.abs(items.length - lastItemsLength.current) > Math.max(items.length, lastItemsLength.current) * 0.3

    if (shouldFullReset) {
      // 完全重置所有状态
      itemHeights.current = {}
      itemPositions.current = []
      setScrollTop(0)
      setShouldAutoScroll(true)

      // 重置滚动位置
      if (containerRef.current) {
        containerRef.current.scrollTop = 0
      }

      calculateItemPositions()
    } else {
      // 轻量重新计算位置
      calculateItemPositions()
    }

    lastItemsLength.current = items.length
  }, [items, calculateItemPositions])

  // 滚动到底部（只在应该自动滚动且有新消息时）
  const scrollToBottom = useCallback(() => {
    if (containerRef.current && shouldAutoScroll && !isUserScrolling) {
      containerRef.current.scrollTop = totalHeight
    }
  }, [totalHeight, shouldAutoScroll, isUserScrolling])

  // 只在有新消息时自动滚动到底部
  useEffect(() => {
    const hasNewItems = items.length > lastItemsLength.current
    lastItemsLength.current = items.length
    
    if (hasNewItems && shouldAutoScroll && !isUserScrolling) {
      // 使用setTimeout确保DOM更新完成后再滚动
      setTimeout(scrollToBottom, 0)
    }
  }, [items.length, scrollToBottom, shouldAutoScroll, isUserScrolling])

  // 总高度变化时，如果用户在底部则跟随
  useEffect(() => {
    if (shouldAutoScroll && !isUserScrolling) {
      setTimeout(scrollToBottom, 0)
    }
  }, [totalHeight, scrollToBottom, shouldAutoScroll, isUserScrolling])

  // 滚动到底部（手动触发）
  const forceScrollToBottom = useCallback(() => {
    setShouldAutoScroll(true)
    setIsUserScrolling(false)
    if (containerRef.current) {
      containerRef.current.scrollTop = totalHeight
    }
  }, [totalHeight])

  // 重置虚拟化状态
  const resetVirtualization = useCallback(() => {
    // 重置所有缓存和状态
    itemHeights.current = {}
    itemPositions.current = []
    setScrollTop(0)
    setShouldAutoScroll(true)
    
    // 重置滚动位置
    if (containerRef.current) {
      containerRef.current.scrollTop = 0
    }
    
    // 重新计算位置
    calculateItemPositions()
  }, [calculateItemPositions])

  // 暴露方法给父组件
  React.useImperativeHandle(ref, () => ({
    scrollToBottom: forceScrollToBottom,
    resetVirtualization: resetVirtualization
  }), [forceScrollToBottom, resetVirtualization])

  return (
    <div
      ref={containerRef}
      style={{
        height,
        overflow: 'auto',
        width: '100%'
      }}
      onScroll={handleScroll}
    >
      <div style={{ height: totalHeight, position: 'relative' }}>
        {visibleItems.map((message, index) => {
          const actualIndex = visibleStart + index
          const top = itemPositions.current[actualIndex] || 0
          return (
            <LogRow
              key={message.id}
              message={message}
              index={actualIndex}
              style={{
                position: 'absolute',
                top,
                left: 0,
                right: 0
              }}
              searchText={searchText}
              onMessageClick={onMessageClick}
              onHeightChange={updateItemHeight}
            />
          )
        })}
      </div>
    </div>
  )
})

// 添加displayName
VirtualizedList.displayName = 'VirtualizedList'

// 日志行组件
interface LogRowProps {
  message: LogMessage
  index: number
  style: React.CSSProperties
  searchText: string
  onMessageClick?: (message: LogMessage) => void
  onHeightChange: (id: string, height: number) => void
}

const LogRow: React.FC<LogRowProps> = ({
  message,
  index,
  style,
  searchText,
  onMessageClick,
  onHeightChange
}) => {
  const rowRef = useRef<HTMLDivElement>(null)
  const lastReportedHeight = useRef<number>(0)

  // 使用ResizeObserver来监测高度变化
  useEffect(() => {
    const element = rowRef.current
    if (!element) return

    // 初始测量
    const height = element.offsetHeight
    if (Math.abs(height - lastReportedHeight.current) > 1) {
      lastReportedHeight.current = height
      onHeightChange(message.id, height)
    }

    // 使用ResizeObserver监听大小变化
    const resizeObserver = new ResizeObserver((entries) => {
      for (const entry of entries) {
        const newHeight = entry.contentRect.height
        // 只有高度变化超过1px时才报告
        if (Math.abs(newHeight - lastReportedHeight.current) > 1) {
          lastReportedHeight.current = newHeight
          onHeightChange(message.id, newHeight)
        }
      }
    })

    resizeObserver.observe(element)

    return () => {
      resizeObserver.disconnect()
    }
  }, [message.id, onHeightChange])

  // 当内容或搜索文本变化时重新测量 - 减少频率
  useEffect(() => {
    const element = rowRef.current
    if (!element) return

    // 使用requestAnimationFrame延迟测量，避免在渲染过程中频繁测量
    requestAnimationFrame(() => {
      const height = element.offsetHeight
      if (Math.abs(height - lastReportedHeight.current) > 1) {
        lastReportedHeight.current = height
        onHeightChange(message.id, height)
      }
    })
  }, [message.id, message.content, searchText, onHeightChange])

  // 高亮搜索文本
  const highlightText = (text: string, search: string) => {
    if (!search) return text
    
    const regex = new RegExp(`(${search.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')})`, 'gi')
    const parts = text.split(regex)
    
    return parts.map((part, index) => 
      regex.test(part) ? (
        <span key={index} className={styles.highlight}>
          {part}
        </span>
      ) : part
    )
  }

  // 获取日志行的CSS类
  const getLogClass = (type: string) => {
    return `${styles.logLine} ${styles[type] || styles.stdout}`
  }

  return (
    <div
      ref={rowRef}
      className={getLogClass(message.type)}
      style={{
        ...style,
        position: 'absolute',
        cursor: onMessageClick ? 'pointer' : 'default'
      }}
      onClick={() => onMessageClick?.(message)}
      title={`${message.type.toUpperCase()} - ${new Date(message.timestamp).toLocaleString()}`}
    >
      <span className={styles.timestamp}>
        [{new Date(message.timestamp).toLocaleTimeString()}]
      </span>
      <span className={styles.logType}>
        [{message.type.toUpperCase()}]
      </span>
      {message.level && (
        <span className={styles.logLevel}>
          [{message.level}]
        </span>
      )}
      {message.source && (
        <span className={styles.logLevel}>
          [{message.source}]
        </span>
      )}
      <span className={styles.content}>{highlightText(message.content, searchText)}</span>
    </div>
  )
}

// 虚拟日志查看器属性
interface VirtualLogViewerProps {
  messages: LogMessage[]
  height?: number
  estimatedItemHeight?: number
  searchable?: boolean
  enableAdvancedFilter?: boolean  // 是否启用高级过滤器
  filterMode?: string  // 过滤模式标识，用于强制重置
  onMessageClick?: (message: LogMessage) => void
  onClear?: () => void
}

const VirtualLogViewer: React.FC<VirtualLogViewerProps> = ({
  messages,
  height = 400,
  estimatedItemHeight = 32, // 增加预估高度以容纳换行
  searchable = true,
  enableAdvancedFilter = false,
  filterMode = 'default', // 默认过滤模式
  onMessageClick,
  onClear
}) => {
  const [searchText, setSearchText] = useState('')
  const [filterResult, setFilterResult] = useState<SearchFilterResult>({
    filteredMessages: messages,
    stats: {
      total: 0,
      filtered: 0,
      stdout: 0,
      stderr: 0,
      errors: 0,
      warnings: 0,
      info: 0,
      debug: 0
    }
  })
  const virtualizedListRef = useRef<VirtualizedListRef>(null)
  const lastFilterMode = useRef(filterMode)

  // 处理过滤结果更新
  const handleFilterChange = useCallback((filteredMessages: LogMessage[], filter: LogFilter) => {
    setFilterResult({
      filteredMessages,
      stats: {
        total: messages.length,
        filtered: filteredMessages.length,
        stdout: filteredMessages.filter(m => m.type === 'stdout').length,
        stderr: filteredMessages.filter(m => m.type === 'stderr').length,
        errors: filteredMessages.filter(m => m.type === 'error' || m.level === 'ERROR').length,
        warnings: filteredMessages.filter(m => m.type === 'warning' || m.level === 'WARNING').length,
        info: filteredMessages.filter(m => m.type === 'info').length,
        debug: filteredMessages.filter(m => m.type === 'debug').length
      }
    })
  }, [messages.length])

  // 获取过滤后的消息 - 优先使用高级过滤器结果，否则使用简单搜索
  const filteredMessages = useMemo(() => {
    if (enableAdvancedFilter) {
      return filterResult.filteredMessages
    }
    
    // 简单搜索逻辑（向后兼容）
    if (!searchText) return messages
    
    const search = searchText.toLowerCase()
    return messages.filter(msg => 
      msg.content.toLowerCase().includes(search) ||
      (msg.source && msg.source.toLowerCase().includes(search)) ||
      (msg.level && msg.level.toLowerCase().includes(search))
    )
  }, [messages, searchText, enableAdvancedFilter, filterResult.filteredMessages])

  // 统计信息 - 使用高级过滤器的统计或计算简单统计
  const stats = useMemo(() => {
    if (enableAdvancedFilter) {
      return filterResult.stats
    }
    
    // 简单统计逻辑（向后兼容）
    return {
      total: messages.length,
      filtered: filteredMessages.length,
      stdout: messages.filter(m => m.type === 'stdout').length,
      stderr: messages.filter(m => m.type === 'stderr').length,
      errors: messages.filter(m => m.type === 'error' || m.level === 'ERROR').length,
      warnings: messages.filter(m => m.type === 'warning' || m.level === 'WARNING').length,
      info: messages.filter(m => m.type === 'info').length,
      debug: messages.filter(m => m.type === 'debug').length
    }
  }, [messages, filteredMessages, enableAdvancedFilter, filterResult.stats])

  // 清除搜索
  const clearSearch = () => {
    setSearchText('')
  }

  // 监听过滤模式变化，强制重置所有状态
  useEffect(() => {
    if (lastFilterMode.current !== filterMode) {
      // 过滤模式发生变化，重置所有状态
      lastFilterMode.current = filterMode
      setSearchText('')
      setFilterResult({
        filteredMessages: messages,
        stats: {
          total: messages.length,
          filtered: messages.length,
          stdout: messages.filter(m => m.type === 'stdout').length,
          stderr: messages.filter(m => m.type === 'stderr').length,
          errors: messages.filter(m => m.type === 'error' || m.level === 'ERROR').length,
          warnings: messages.filter(m => m.type === 'warning' || m.level === 'WARNING').length,
          info: messages.filter(m => m.type === 'info').length,
          debug: messages.filter(m => m.type === 'debug').length
        }
      })

      // 强制重置虚拟化状态
      if (virtualizedListRef.current) {
        virtualizedListRef.current.resetVirtualization()
      }
    }
  }, [filterMode, messages])

  // 在messages或filterMode变化时强制更新
  const memoKey = useMemo(() => {
    return `${filterMode}-${messages.length}-${filteredMessages.length}-${searchText}`
  }, [filterMode, messages.length, filteredMessages.length, searchText])

  return (
    <Card
      title={
        <Space>
          <span>虚拟日志查看器</span>
          <Tag color="blue">总计: {stats.total}</Tag>
          <Tag color="green">正常: {stats.stdout}</Tag>
          <Tag color="red">错误: {stats.stderr}</Tag>
          {searchText && <Tag color="orange">已过滤: {stats.filtered}</Tag>}
        </Space>
      }
      extra={
        <Space>
          {onClear && (
            <Button
              icon={<ClearOutlined />}
              onClick={onClear}
              size="small"
            >
              清除
            </Button>
          )}
          <Button
            onClick={() => {
              virtualizedListRef.current?.scrollToBottom()
            }}
            size="small"
          >
            滚动到底部
          </Button>
        </Space>
      }
    >
      {/* 搜索栏 - 根据是否启用高级过滤器显示不同的搜索界面 */}
      {searchable && (
        <div style={{ marginBottom: 12 }}>
          {enableAdvancedFilter ? (
            <LogSearchFilter
              messages={messages}
              onFilterChange={handleFilterChange}
              showAdvanced={true}
            />
          ) : (
            <Search
              placeholder="搜索日志内容、来源或级别..."
              value={searchText}
              onChange={(e) => setSearchText(e.target.value)}
              onSearch={setSearchText}
              allowClear
              size="small"
              suffix={
                searchText && (
                  <Button
                    type="text"
                    icon={<ClearOutlined />}
                    onClick={clearSearch}
                    size="small"
                  />
                )
              }
            />
          )}
        </div>
      )}

      {/* 虚拟列表 */}
      <div className={styles.logContainer} style={{ height, overflow: 'hidden' }}>
        {filteredMessages.length === 0 ? (
          <div className={styles.emptyState}>
            {messages.length === 0 ? '暂无日志消息' : '没有匹配的日志消息'}
          </div>
        ) : (
          <VirtualizedList
            key={memoKey}
            ref={virtualizedListRef}
            items={filteredMessages}
            height={height}
            estimatedItemHeight={estimatedItemHeight}
            searchText={searchText}
            onMessageClick={onMessageClick}
          />
        )}
      </div>

      {/* 底部统计 */}
      <div
        style={{
          marginTop: 8,
          padding: '4px 0',
          fontSize: '12px',
          color: '#666',
          borderTop: '1px solid #f0f0f0'
        }}
      >
        显示 {stats.filtered} / {stats.total} 条日志
        {searchText && ` (搜索: "${searchText}")`}
        {stats.errors > 0 && (
          <span style={{ color: '#ff4d4f', marginLeft: 16 }}>
            错误: {stats.errors}
          </span>
        )}
        {stats.warnings > 0 && (
          <span style={{ color: '#faad14', marginLeft: 8 }}>
            警告: {stats.warnings}
          </span>
        )}
      </div>
    </Card>
  )
}

export default VirtualLogViewer
