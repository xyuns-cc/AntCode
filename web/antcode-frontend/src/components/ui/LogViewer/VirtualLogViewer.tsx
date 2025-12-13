import React, { useState, useEffect, useRef, useCallback, useMemo } from 'react'
import { Card, Input, Space, Tag, Button, theme } from 'antd'
import { ClearOutlined } from '@ant-design/icons'
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
  const [, setPositionsVersion] = useState(0) // 用于触发重渲染
  const lastScrollTop = useRef(0)
  const containerRef = useRef<HTMLDivElement>(null)
  const itemHeights = useRef<Record<string, number>>({})
  const itemPositions = useRef<number[]>([])
  const [totalHeight, setTotalHeight] = useState(0)
  const lastItemsLength = useRef(items.length)
  // 追踪已渲染过的消息ID，用于判断是否为新消息
  const renderedIds = useRef<Set<string>>(new Set())
  // 用户是否正在滚动（防止自动滚动干扰）
  const userScrollingRef = useRef(false)
  const userScrollTimeoutRef = useRef<NodeJS.Timeout | null>(null)
  const recalcTimeoutRef = useRef<NodeJS.Timeout | null>(null)

  // 计算每个项目的位置
  const calculateItemPositions = useCallback(() => {
    let position = 0
    const positions: number[] = []

    for (let i = 0; i < items.length; i++) {
      positions[i] = position
      const message = items[i]
      const h = itemHeights.current[message?.id ?? ''] || estimatedItemHeight
      position += h
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
  
  // 待更新的高度队列
  const pendingHeightUpdates = useRef<boolean>(false)
  
  // 更新项目高度 - 添加防抖避免频繁重新计算
  const updateItemHeight = useCallback((id: string, h: number) => {
    if (!id || h <= 0) return
    const currentHeight = itemHeights.current[id]

    // 只有高度变化超过1px时才更新，避免微小差异导致频繁重计算
    if (Math.abs((currentHeight || 0) - h) > 1) {
      itemHeights.current[id] = h
      
      // 标记有待处理的高度更新
      pendingHeightUpdates.current = true

      // 用户正在滚动时，延迟位置重计算，但仍然记录高度
      if (userScrollingRef.current) {
        return
      }

      // 使用requestAnimationFrame避免在滚动过程中频繁更新布局
      requestAnimationFrame(() => {
        if (!userScrollingRef.current && pendingHeightUpdates.current) {
          pendingHeightUpdates.current = false
          calculateItemPositions()
        }
      })
    }
  }, [calculateItemPositions])
  
  // 计算可见范围 - 增加缓冲区减少滚动时的重渲染
  const getVisibleRange = useCallback(() => {
    const positions = itemPositions.current
    let start = 0
    let end = items.length - 1
    const buffer = 3 // 增加缓冲区，提前渲染更多项

    // 找到第一个可见项
    for (let i = 0; i < positions.length; i++) {
      const message = items[i]
      const h = itemHeights.current[message?.id ?? ''] || estimatedItemHeight
      if (positions[i] + h >= scrollTop) {
        start = Math.max(0, i - buffer)
        break
      }
    }

    // 找到最后一个可见项
    for (let i = start; i < positions.length; i++) {
      const message = items[i]
      const itemHeight = itemHeights.current[message?.id ?? ''] || estimatedItemHeight
      if (positions[i] + itemHeight >= scrollTop + height) {
        end = Math.min(items.length - 1, i + buffer)
        break
      }
    }
    
    return { start, end }
  }, [items, scrollTop, height, estimatedItemHeight])

  const { start: visibleStart, end: visibleEnd } = getVisibleRange()
  const visibleItems = items.slice(visibleStart, visibleEnd + 1)

  // 处理滚动 - 简化逻辑，减少抖动
  const handleScroll = useCallback((e: React.UIEvent<HTMLDivElement>) => {
    const container = e.currentTarget
    const newScrollTop = container.scrollTop
    const scrollHeight = container.scrollHeight
    const clientHeight = container.clientHeight
    
    // 计算是否在底部附近
    const distanceToBottom = scrollHeight - newScrollTop - clientHeight
    const isAtBottom = distanceToBottom < 50
    
    // 标记用户正在滚动
    userScrollingRef.current = true
    if (userScrollTimeoutRef.current) {
      clearTimeout(userScrollTimeoutRef.current)
    }
    // 用户停止滚动 300ms 后才允许自动滚动和位置更新
    userScrollTimeoutRef.current = setTimeout(() => {
      userScrollingRef.current = false
      // 用户停止滚动后，更新 scrollTop 并重新计算位置
      const currentScrollTop = containerRef.current?.scrollTop || 0
      setScrollTop(currentScrollTop)
      // 如果有待处理的高度更新，重新计算位置
      if (pendingHeightUpdates.current) {
        pendingHeightUpdates.current = false
        calculateItemPositions()
      }
    }, 300)

    // 用户向上滚动时，禁用自动滚动
    if (newScrollTop < lastScrollTop.current - 10) {
      setShouldAutoScroll(false)
    }
    // 用户滚动到底部时，重新启用自动滚动
    else if (isAtBottom) {
      setShouldAutoScroll(true)
    }

    lastScrollTop.current = newScrollTop
    
    // 只有在用户不是主动滚动时才更新 scrollTop 状态
    // 这样可以避免在用户滚动时触发重新渲染
    if (!userScrollingRef.current || Math.abs(newScrollTop - scrollTop) > 100) {
      setScrollTop(newScrollTop)
    }
  }, [scrollTop, calculateItemPositions])

  // 初始化位置计算 - 延迟执行确保 DOM 已渲染
  useEffect(() => {
    // 首次渲染后延迟计算，确保高度已测量
    const timer = setTimeout(() => {
      calculateItemPositions()
    }, 50)
    return () => clearTimeout(timer)
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
      itemHeights.current = {}
      itemPositions.current = []
      renderedIds.current = new Set()
      setScrollTop(0)
      setShouldAutoScroll(true)

      if (containerRef.current) {
        containerRef.current.scrollTop = 0
      }

      calculateItemPositions()
    } else {
      calculateItemPositions()
    }

    lastItemsLength.current = items.length
  }, [items, calculateItemPositions])

  // 只在有新消息且允许自动滚动时，滚动到底部
  const prevItemsLength = useRef(items.length)
  useEffect(() => {
    const hasNewItems = items.length > prevItemsLength.current
    prevItemsLength.current = items.length
    
    // 只有新增消息、允许自动滚动、且用户没有在滚动时才自动滚动
    if (hasNewItems && shouldAutoScroll && !userScrollingRef.current) {
      // 使用 RAF 确保在下一帧滚动
      requestAnimationFrame(() => {
        if (containerRef.current && !userScrollingRef.current) {
          containerRef.current.scrollTop = containerRef.current.scrollHeight
        }
      })
    }
  }, [items.length, shouldAutoScroll])

  // 滚动到底部（手动触发）
  const forceScrollToBottom = useCallback(() => {
    setShouldAutoScroll(true)
    userScrollingRef.current = false
    if (userScrollTimeoutRef.current) {
      clearTimeout(userScrollTimeoutRef.current)
      userScrollTimeoutRef.current = null
    }
    requestAnimationFrame(() => {
      if (containerRef.current) {
        containerRef.current.scrollTop = containerRef.current.scrollHeight
      }
    })
  }, [])

  // 重置虚拟化状态
  const resetVirtualization = useCallback(() => {
    itemHeights.current = {}
    itemPositions.current = []
    renderedIds.current = new Set()
    setScrollTop(0)
    setShouldAutoScroll(true)
    userScrollingRef.current = false
    
    if (userScrollTimeoutRef.current) {
      clearTimeout(userScrollTimeoutRef.current)
      userScrollTimeoutRef.current = null
    }
    if (recalcTimeoutRef.current) {
      clearTimeout(recalcTimeoutRef.current)
      recalcTimeoutRef.current = null
    }
    
    if (containerRef.current) {
      containerRef.current.scrollTop = 0
    }
    
    calculateItemPositions()
  }, [calculateItemPositions])
  
  // 清理定时器
  useEffect(() => {
    return () => {
      if (userScrollTimeoutRef.current) {
        clearTimeout(userScrollTimeoutRef.current)
      }
      if (recalcTimeoutRef.current) {
        clearTimeout(recalcTimeoutRef.current)
      }
    }
  }, [])

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
      <div style={{ height: totalHeight + 60, position: 'relative', paddingBottom: 60 }}>
        {visibleItems.map((message, index) => {
          const actualIndex = visibleStart + index
          const top = itemPositions.current[actualIndex] || 0
          // 判断是否为新渲染的消息
          const isNew = !renderedIds.current.has(message.id)
          if (isNew) {
            renderedIds.current.add(message.id)
          }
          return (
            <LogRow
              key={message.id}
              message={message}
              top={top}
              isNew={isNew}
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

VirtualizedList.displayName = 'VirtualizedList'

// 日志行组件
interface LogRowProps {
  message: LogMessage
  top: number
  isNew: boolean
  searchText: string
  onMessageClick?: (message: LogMessage) => void
  onHeightChange: (id: string, height: number) => void
}

const LogRow = React.memo<LogRowProps>(({
  message,
  top,
  isNew,
  searchText,
  onMessageClick,
  onHeightChange
}) => {
  const rowRef = useRef<HTMLDivElement>(null)
  const lastReportedHeight = useRef<number>(0)
  const [showAnimation, setShowAnimation] = useState(isNew)

  // 新消息入场动画
  useEffect(() => {
    if (isNew && showAnimation) {
      const timer = setTimeout(() => {
        setShowAnimation(false)
      }, 300)
      return () => clearTimeout(timer)
    }
    return undefined
  }, [isNew, showAnimation])

  // 使用 useLayoutEffect 确保在绘制前测量高度
  React.useLayoutEffect(() => {
    const element = rowRef.current
    if (!element) return

    // 使用 offsetHeight 获取完整高度（包含 padding）
    const h = element.offsetHeight
    if (h > 0 && Math.abs(h - lastReportedHeight.current) > 1) {
      lastReportedHeight.current = h
      onHeightChange(message.id, h)
    }
  }, [message.id, message.content, onHeightChange])

  // 使用 ResizeObserver 监测后续高度变化
  useEffect(() => {
    const element = rowRef.current
    if (!element) return

    const resizeObserver = new ResizeObserver((entries) => {
      for (const entry of entries) {
        const newHeight = (entry.target as HTMLElement).offsetHeight
        if (newHeight > 0 && Math.abs(newHeight - lastReportedHeight.current) > 1) {
          lastReportedHeight.current = newHeight
          onHeightChange(message.id, newHeight)
        }
      }
    })

    resizeObserver.observe(element)
    return () => resizeObserver.disconnect()
  }, [message.id, onHeightChange])

  // 高亮搜索文本 - 使用useMemo缓存
  const highlightedContent = useMemo(() => {
    if (!searchText) return message.content
    
    try {
      const escaped = searchText.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')
      const regex = new RegExp(`(${escaped})`, 'gi')
      const parts = message.content.split(regex)
      
      return parts.map((part, idx) => 
        regex.test(part) ? (
          <span key={idx} className={styles.highlight}>{part}</span>
        ) : part
      )
    } catch {
      return message.content
    }
  }, [message.content, searchText])

  // 缓存CSS类名
  const logClassName = useMemo(() => {
    const baseClass = `${styles.logLine} ${styles[message.type] || styles.stdout}`
    return showAnimation ? `${baseClass} ${styles.newItem}` : baseClass
  }, [message.type, showAnimation])

  // 缓存时间戳
  const formattedTime = useMemo(() => 
    new Date(message.timestamp).toLocaleTimeString()
  , [message.timestamp])

  const formattedDateTime = useMemo(() => 
    new Date(message.timestamp).toLocaleString()
  , [message.timestamp])

  // 缓存点击处理
  const handleClick = useCallback(() => {
    onMessageClick?.(message)
  }, [onMessageClick, message])

  return (
    <div
      ref={rowRef}
      className={logClassName}
      style={{
        position: 'absolute',
        top,
        left: 0,
        right: 0,
        cursor: onMessageClick ? 'pointer' : 'default'
      }}
      onClick={handleClick}
      title={`${message.type.toUpperCase()} - ${formattedDateTime}`}
    >
      <span className={styles.timestamp}>[{formattedTime}]</span>
      <span className={styles.logType}>[{message.type.toUpperCase()}]</span>
      {message.level && <span className={styles.logLevel}>[{message.level}]</span>}
      {message.source && <span className={styles.logLevel}>[{message.source}]</span>}
      <span className={styles.content}>{highlightedContent}</span>
    </div>
  )
}, (prevProps, nextProps) => {
  // 自定义比较：只在关键属性变化时重新渲染
  return (
    prevProps.message.id === nextProps.message.id &&
    prevProps.message.content === nextProps.message.content &&
    prevProps.top === nextProps.top &&
    prevProps.searchText === nextProps.searchText &&
    prevProps.isNew === nextProps.isNew
  )
})

LogRow.displayName = 'LogRow'


// 虚拟日志查看器属性
interface VirtualLogViewerProps {
  messages: LogMessage[]
  height?: number
  estimatedItemHeight?: number
  searchable?: boolean
  enableAdvancedFilter?: boolean
  filterMode?: string
  onMessageClick?: (message: LogMessage) => void
  onClear?: () => void
}

const VirtualLogViewer: React.FC<VirtualLogViewerProps> = ({
  messages,
  height = 400,
  estimatedItemHeight = 28,
  searchable = true,
  enableAdvancedFilter = false,
  filterMode = 'default',
  onMessageClick,
  onClear
}) => {
  const { token } = theme.useToken()
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
  const handleFilterChange = useCallback((filteredMessages: LogMessage[], _filter: LogFilter) => {
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
        debug: filteredMessages.filter(m => m.level === 'DEBUG').length
      }
    })
  }, [messages.length])

  // 获取过滤后的消息
  const filteredMessages = useMemo(() => {
    if (enableAdvancedFilter) {
      return filterResult.filteredMessages
    }
    
    if (!searchText) return messages
    
    const search = searchText.toLowerCase()
    return messages.filter(msg => 
      msg.content.toLowerCase().includes(search) ||
      (msg.source && msg.source.toLowerCase().includes(search)) ||
      (msg.level && msg.level.toLowerCase().includes(search))
    )
  }, [messages, searchText, enableAdvancedFilter, filterResult.filteredMessages])

  // 统计信息
  const stats = useMemo(() => {
    if (enableAdvancedFilter) {
      return filterResult.stats
    }
    
    return {
      total: messages.length,
      filtered: filteredMessages.length,
      stdout: messages.filter(m => m.type === 'stdout').length,
      stderr: messages.filter(m => m.type === 'stderr').length,
      errors: messages.filter(m => m.type === 'error' || m.level === 'ERROR').length,
      warnings: messages.filter(m => m.type === 'warning' || m.level === 'WARNING').length,
      info: messages.filter(m => m.type === 'info').length,
      debug: messages.filter(m => m.level === 'DEBUG').length
    }
  }, [messages, filteredMessages, enableAdvancedFilter, filterResult.stats])

  // 清除搜索
  const clearSearch = () => {
    setSearchText('')
  }

  // 监听过滤模式变化
  useEffect(() => {
    if (lastFilterMode.current !== filterMode) {
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
          debug: messages.filter(m => m.level === 'DEBUG').length
        }
      })

      if (virtualizedListRef.current) {
        virtualizedListRef.current.resetVirtualization()
      }
    }
  }, [filterMode, messages])

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
            onClick={() => virtualizedListRef.current?.scrollToBottom()}
            size="small"
          >
            滚动到底部
          </Button>
        </Space>
      }
    >
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

      <div className={styles.logContainer} style={{ height, overflow: 'hidden' }}>
        {filteredMessages.length === 0 ? (
          <div className={styles.emptyState}>
            {messages.length === 0 ? '暂无日志消息' : '没有匹配的日志消息'}
          </div>
        ) : (
          <VirtualizedList
            ref={virtualizedListRef}
            items={filteredMessages}
            height={height}
            estimatedItemHeight={estimatedItemHeight}
            searchText={searchText}
            onMessageClick={onMessageClick}
          />
        )}
      </div>

      <div
        style={{
          marginTop: 8,
          padding: '4px 0',
          fontSize: '12px',
          color: token.colorTextSecondary,
          borderTop: `1px solid ${token.colorBorderSecondary}`
        }}
      >
        显示 {stats.filtered} / {stats.total} 条日志
        {searchText && ` (搜索: "${searchText}")`}
        {stats.errors > 0 && (
          <span style={{ color: token.colorError, marginLeft: 16 }}>
            错误: {stats.errors}
          </span>
        )}
        {stats.warnings > 0 && (
          <span style={{ color: token.colorWarning, marginLeft: 8 }}>
            警告: {stats.warnings}
          </span>
        )}
      </div>
    </Card>
  )
}

export default VirtualLogViewer
