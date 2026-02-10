import React, { useState, useEffect, useRef, useCallback, useMemo } from 'react'
import {
  Button,
  Space,
  Tag,
  Input,
  Select,
  Row,
  Col,
  Dropdown,
  message,
  Checkbox,
  Typography,
  theme
} from 'antd'
import {
  PlayCircleOutlined,
  StopOutlined,
  ClearOutlined,
  ReloadOutlined,
  DownloadOutlined,
  FullscreenOutlined,
  FullscreenExitOutlined
} from '@ant-design/icons'
import { STORAGE_KEYS } from '@/utils/constants'
import { logService } from '@/services/logs'
import { LogExporter } from '@/utils/logExport'
import Logger from '@/utils/logger'
import VirtualLogViewer from './VirtualLogViewer'
import styles from './LogViewer.module.css'

const { Search } = Input
const { Option } = Select
const { Text } = Typography

const DEFAULT_LEVELS = ['DEBUG', 'INFO', 'WARNING', 'ERROR', 'CRITICAL']
const DEFAULT_TYPES = ['stdout', 'stderr']

// 用于生成唯一ID的计数器
let idCounter = 0

// 生成唯一ID的函数
const generateUniqueId = (): string => {
  idCounter += 1
  return `${Date.now()}-${idCounter}-${Math.random().toString(36).substr(2, 9)}`
}

// 日志消息接口
interface LogMessage {
  id: string
  type: 'stdout' | 'stderr' | 'info' | 'error' | 'warning' | 'success'
  content: string
  timestamp: string
  level?: string
  source?: string
  raw?: unknown
}

// 执行状态更新接口
interface ExecutionStatusUpdate {
  status: string
  message?: string
  progress?: number
}

// 组件属性接口
interface EnhancedLogViewerProps {
  executionId: string
  height?: number
  showControls?: boolean
  autoConnect?: boolean
  showStdout?: boolean
  showStderr?: boolean
  maxLines?: number
  enableSearch?: boolean
  enableExport?: boolean
  enableVirtualization?: boolean
  onLogUpdate?: (logs: string[]) => void // 新增：日志更新回调
  onStatusUpdate?: (status: ExecutionStatusUpdate) => void // 新增：状态更新回调
}

// WebSocket 连接状态
type ConnectionStatus = 'disconnected' | 'connecting' | 'connected' | 'error'

const EnhancedLogViewer: React.FC<EnhancedLogViewerProps> = ({
  executionId,
  height = 600,
  showControls = true,
  autoConnect = true,
  showStdout = true,
  showStderr = true,
  maxLines = 1000,
  enableSearch = true,
  enableExport = true,
  enableVirtualization = false,
  onLogUpdate,
  onStatusUpdate
}) => {
  const { token } = theme.useToken() // 添加主题支持

  // 状态管理
  const [ws, setWs] = useState<WebSocket | null>(null)
  const [connectionStatus, setConnectionStatus] = useState<ConnectionStatus>('disconnected')
  const [messages, setMessages] = useState<LogMessage[]>([])
  const [isAutoScroll, setIsAutoScroll] = useState(true)
  const [isPaused, setIsPaused] = useState(false)
  const [isFullscreen, setIsFullscreen] = useState(false)
  
  // 过滤和搜索状态
  const [searchText, setSearchText] = useState('')
  const [selectedLevels, setSelectedLevels] = useState<string[]>([...DEFAULT_LEVELS])
  const [selectedTypes, setSelectedTypes] = useState<string[]>([...DEFAULT_TYPES])
  
  // 统计信息
  const [stats, setStats] = useState({
    total: 0,
    stdout: 0,
    stderr: 0,
    errors: 0,
    warnings: 0
  })

  // 引用
  const logContainerRef = useRef<HTMLDivElement>(null)
  const mountedRef = useRef(true)
  const reconnectTimeoutRef = useRef<NodeJS.Timeout | null>(null)
  const tryFallbackConnectionRef = useRef<(() => void) | null>(null)

  // 确保组件挂载时mountedRef为true
  useEffect(() => {
    mountedRef.current = true
    
    return () => {
      mountedRef.current = false
    }
  }, [])

  // 通知父组件日志更新
  useEffect(() => {
    if (onLogUpdate && messages.length > 0) {
      const logContents = messages.map(msg => msg.content)
      onLogUpdate(logContents)
    }
  }, [messages, onLogUpdate])

  // 添加日志消息
  const addLogMessage = useCallback((logMessage: LogMessage) => {
    if (!mountedRef.current || isPaused) {
      return
    }

    setMessages(prev => {
      const newMessages = [...prev, logMessage]
      // 限制消息数量，避免内存泄漏
      if (newMessages.length > maxLines) {
        return newMessages.slice(-Math.floor(maxLines * 0.8))
      }
      return newMessages
    })
  }, [isPaused, maxLines])

  // WebSocket 连接（使用新的连接管理）
  const connect = useCallback(async () => {
    if (connectionStatus === 'connected' || connectionStatus === 'connecting') {
      return
    }
    
    // 如果已有连接，先清理
    if (ws && ws.readyState !== WebSocket.CLOSED) {
      ws.close(1000, 'Reconnecting')
      setWs(null)
    }

    try {
      setConnectionStatus('connecting')
      addLogMessage({
        id: generateUniqueId(),
        type: 'info',
        content: `正在连接到执行ID: ${executionId}`,
        timestamp: new Date().toISOString()
      })

      // 使用新的WebSocket连接管理
      const ws = logService.connectLogStream(
        executionId,
        (logEntry) => {
          // 将LogEntry转换为LogMessage格式
          const logMessage: LogMessage = {
            id: generateUniqueId(),
            type: logEntry.log_type as 'stdout' | 'stderr',
            content: logEntry.message,
            timestamp: logEntry.timestamp,
            level: logEntry.level,
            source: logEntry.source,
            raw: logEntry
          }
          
          // 根据设置过滤日志类型
          if ((logMessage.type === 'stdout' && !showStdout) || 
              (logMessage.type === 'stderr' && !showStderr)) {
            return
          }
          
          addLogMessage(logMessage)
        },
        (error) => {
          console.error('WebSocket连接错误:', error)
          setConnectionStatus('error')
          addLogMessage({
            id: generateUniqueId(),
            type: 'error',
            content: `[错误] WebSocket连接错误: ${error instanceof Error ? error.message : String(error)}`,
            timestamp: new Date().toISOString()
          })
        },
        undefined, // onStateChange
        (statusUpdate) => {
          // 处理执行状态更新
          Logger.info('收到执行状态更新:', statusUpdate)
          
          // 添加状态变更日志
          addLogMessage({
            id: generateUniqueId(),
            type: 'info',
            content: `[状态] ${statusUpdate.message || statusUpdate.status}`,
            timestamp: new Date().toISOString()
          })
          
          // 调用外部回调
          onStatusUpdate?.(statusUpdate)
        }
      )

      if (ws) {
        setWs(ws)
        setConnectionStatus('connected')
        addLogMessage({
          id: generateUniqueId(),
          type: 'success',
          content: '[成功] WebSocket连接已建立',
          timestamp: new Date().toISOString()
        })
      } else {
        throw new Error('无法创建WebSocket连接')
      }

    } catch (error) {
      console.error('WebSocket creation failed:', error)
      setConnectionStatus('error')
      addLogMessage({
        id: generateUniqueId(),
        type: 'error',
        content: `创建WebSocket失败: ${error instanceof Error ? error.message : String(error)}`,
        timestamp: new Date().toISOString()
      })

      // 尝试备用连接方案
      tryFallbackConnectionRef.current?.()
    }
  }, [executionId, connectionStatus, showStdout, showStderr, addLogMessage, onStatusUpdate, ws])

  // 备用连接方案
  const tryFallbackConnection = useCallback(async () => {
    const token = localStorage.getItem(STORAGE_KEYS.ACCESS_TOKEN)
    if (!token || !executionId) return

    addLogMessage({
      id: generateUniqueId(),
      type: 'info',
      content: '[重试] 尝试备用WebSocket连接...',
      timestamp: new Date().toISOString()
    })

    // 使用正确的WebSocket端点
    const fallbackUrl = `ws://localhost:8000/api/v1/ws/executions/${executionId}/logs?token=${encodeURIComponent(token)}`

    try {
      const fallbackWs = new WebSocket(fallbackUrl)
      setWs(fallbackWs)

      fallbackWs.onopen = () => {
        if (!mountedRef.current) return
        setConnectionStatus('connected')
        addLogMessage({
          id: generateUniqueId(),
          type: 'success',
          content: '[成功] 备用WebSocket连接已建立',
          timestamp: new Date().toISOString()
        })
      }

      fallbackWs.onmessage = (event) => {
        if (!mountedRef.current) return

        try {
          const data = JSON.parse(event.data)

          // 处理简化端点的消息格式
          if (data.type === 'log') {
            addLogMessage({
              id: generateUniqueId(),
              type: data.log_type || 'stdout',
              content: data.message || data.content,
              timestamp: data.timestamp || new Date().toISOString(),
              level: data.level,
              source: data.source
            })
          } else {
            addLogMessage({
              id: generateUniqueId(),
              type: 'info',
              content: `收到消息: ${JSON.stringify(data)}`,
              timestamp: new Date().toISOString()
            })
          }
        } catch (error) {
          addLogMessage({
            id: generateUniqueId(),
            type: 'error',
            content: `解析备用连接消息失败: ${error instanceof Error ? error.message : String(error)}`,
            timestamp: new Date().toISOString()
          })
        }
      }

      fallbackWs.onerror = (error) => {
        if (!mountedRef.current) return
        console.error('Fallback WebSocket error:', error)
        setConnectionStatus('error')
        addLogMessage({
          id: generateUniqueId(),
          type: 'error',
          content: '[失败] 备用WebSocket连接也失败了',
          timestamp: new Date().toISOString()
        })
      }

      fallbackWs.onclose = (event) => {
        if (!mountedRef.current) return
        setConnectionStatus('disconnected')
        setWs(null)
        addLogMessage({
          id: generateUniqueId(),
          type: 'warning',
          content: `[断开] 备用WebSocket连接已关闭 (${event.code})`,
          timestamp: new Date().toISOString()
        })
      }

    } catch (error) {
      console.error('Fallback WebSocket creation failed:', error)
      addLogMessage({
        id: generateUniqueId(),
        type: 'error',
        content: `创建备用WebSocket失败: ${error instanceof Error ? error.message : String(error)}`,
        timestamp: new Date().toISOString()
      })
    }
  }, [executionId, addLogMessage])

  // 更新 ref 以便 connect 函数可以调用
  useEffect(() => {
    tryFallbackConnectionRef.current = tryFallbackConnection
  }, [tryFallbackConnection])

  // 断开连接
  const disconnect = useCallback(() => {
    if (reconnectTimeoutRef.current) {
      clearTimeout(reconnectTimeoutRef.current)
      reconnectTimeoutRef.current = null
    }
    
    if (ws) {
      if (ws.readyState === WebSocket.OPEN || ws.readyState === WebSocket.CONNECTING) {
        ws.close(1000, 'Manual disconnect')
      }
      setWs(null)
    }
    
    setConnectionStatus('disconnected')
    addLogMessage({
      id: generateUniqueId(),
      type: 'info',
      content: '[断开] 手动断开连接',
      timestamp: new Date().toISOString()
    })
  }, [ws, addLogMessage])

  // 清除日志
  const clearLogs = useCallback(() => {
    setMessages([])
    addLogMessage({
      id: generateUniqueId(),
      type: 'info',
      content: '[清除] 日志已清除',
      timestamp: new Date().toISOString()
    })
  }, [addLogMessage])

  const resetBasicFilters = useCallback(() => {
    setSearchText('')
    setSelectedLevels([...DEFAULT_LEVELS])
    setSelectedTypes([...DEFAULT_TYPES])
  }, [])

  // 基础过滤消息
  const displayMessages = useMemo(() => {
    let filtered = messages

    // 搜索过滤
    if (searchText) {
      const search = searchText.toLowerCase()
      filtered = filtered.filter(msg =>
        msg.content.toLowerCase().includes(search) ||
        (msg.source && msg.source.toLowerCase().includes(search))
      )
    }

    // 级别过滤
    if (selectedLevels.length < 5) {
      filtered = filtered.filter(msg =>
        !msg.level || selectedLevels.includes(msg.level)
      )
    }

    // 类型过滤
    if (selectedTypes.length < 2) {
      filtered = filtered.filter(msg => selectedTypes.includes(msg.type))
    }

    return filtered
  }, [messages, searchText, selectedLevels, selectedTypes])

  // 导出日志
  const exportLogs = useCallback(async (format: 'txt' | 'json' | 'csv') => {
    if (displayMessages.length === 0) {
      message.warning('没有日志可导出')
      return
    }

    try {
      // 优先导出原始日志文件
      await LogExporter.exportLogFile({
        executionId,
        format,
        includeStdout: showStdout,
        includeStderr: showStderr,
        includeTimestamp: true,
        includeLevel: true,
        includeSource: true,
        maxLines: maxLines
      })
      message.success(`日志已导出为 ${format.toUpperCase()} 格式`)
    } catch (error) {
      // 如果导出文件失败，则导出当前显示的消息
      console.warn('导出原始日志失败，使用当前显示的消息:', error)

      let content = ''
      let filename = `logs_${executionId}_${new Date().toISOString().split('T')[0]}`

      switch (format) {
        case 'txt':
          content = displayMessages.map(msg =>
            `[${new Date(msg.timestamp).toLocaleString()}] [${msg.type.toUpperCase()}] ${msg.content}`
          ).join('\n')
          filename += '.txt'
          break

        case 'json':
          content = JSON.stringify(displayMessages, null, 2)
          filename += '.json'
          break

        case 'csv': {
          const headers = 'Timestamp,Type,Level,Source,Content\n'
          const rows = displayMessages.map(msg =>
            `"${msg.timestamp}","${msg.type}","${msg.level || ''}","${msg.source || ''}","${msg.content.replace(/"/g, '""')}"`
          ).join('\n')
          content = headers + rows
          filename += '.csv'
          break
        }
      }

      // 创建下载链接
      const blob = new Blob([content], { type: 'text/plain;charset=utf-8' })
      const url = URL.createObjectURL(blob)
      const link = document.createElement('a')
      link.href = url
      link.download = filename
      document.body.appendChild(link)
      link.click()
      document.body.removeChild(link)
      URL.revokeObjectURL(url)

      message.success(`日志已导出为 ${format.toUpperCase()} 格式`)
    }
  }, [displayMessages, executionId, showStdout, showStderr, maxLines])

  // 更新统计信息
  useEffect(() => {
    const newStats = {
      total: messages.length,
      stdout: messages.filter(m => m.type === 'stdout').length,
      stderr: messages.filter(m => m.type === 'stderr').length,
      errors: messages.filter(m => m.type === 'error' || m.level === 'ERROR').length,
      warnings: messages.filter(m => m.type === 'warning' || m.level === 'WARNING').length
    }
    setStats(newStats)
  }, [messages])

  const statsItems = useMemo(
    () => [
      { key: 'total', title: '总计', value: stats.total },
      { key: 'stdout', title: '正常输出', value: stats.stdout, color: token.colorSuccess },
      { key: 'stderr', title: '错误输出', value: stats.stderr, color: token.colorError },
      { key: 'errors', title: '错误', value: stats.errors, color: token.colorError },
      { key: 'warnings', title: '警告', value: stats.warnings, color: token.colorWarning },
      { key: 'filtered', title: '已过滤', value: displayMessages.length }
    ],
    [
      stats.total,
      stats.stdout,
      stats.stderr,
      stats.errors,
      stats.warnings,
      displayMessages.length,
      token.colorError,
      token.colorSuccess,
      token.colorWarning
    ]
  )

  // 自动滚动到底部
  useEffect(() => {
    if (isAutoScroll && !isPaused && logContainerRef.current) {
      logContainerRef.current.scrollTop = logContainerRef.current.scrollHeight
    }
  }, [displayMessages, isAutoScroll, isPaused])

  // 自动连接（添加防抖和重复连接保护）
  useEffect(() => {
    if (!autoConnect || !executionId) return
    
    // 只在真正断开时才自动连接，避免重复连接
    if (connectionStatus === 'disconnected' && !ws) {
      const timeoutId = setTimeout(() => {
        connect()
      }, 500) // 500ms 防抖
      
      return () => clearTimeout(timeoutId)
    }
  }, [autoConnect, executionId, connectionStatus, ws, connect])

  // 组件卸载清理
  useEffect(() => {
    return () => {
      if (reconnectTimeoutRef.current) {
        clearTimeout(reconnectTimeoutRef.current)
        reconnectTimeoutRef.current = null
      }
      
      if (ws && ws.readyState === WebSocket.OPEN) {
        ws.close(1000, 'Component unmount')
      }
    }
  }, [ws])

  // 渲染日志消息
  const renderLogMessage = (msg: LogMessage) => {
    let color = token.colorSuccess
    let backgroundColor = 'transparent'

    switch (msg.type) {
      case 'stderr':
      case 'error':
        color = token.colorError
        backgroundColor = `${token.colorError}15`
        break
      case 'warning':
        color = token.colorWarning
        backgroundColor = `${token.colorWarning}15`
        break
      case 'info':
        color = token.colorPrimary
        backgroundColor = `${token.colorPrimary}15`
        break
      case 'success':
        color = token.colorSuccess
        backgroundColor = `${token.colorSuccess}15`
        break
      case 'stdout':
      default:
        color = token.colorSuccess
        backgroundColor = 'transparent'
    }

    return (
      <div
        key={msg.id}
        className={styles.logLine}
        style={{
          padding: '4px 8px',
          margin: '1px 0',
          fontFamily: 'Monaco, Consolas, "Courier New", monospace',
          fontSize: '12px',
          color,
          backgroundColor,
          borderLeft: `3px solid ${color}`,
          wordBreak: 'break-all',
          whiteSpace: 'pre-wrap'
        }}
      >
        <span style={{ color: token.colorTextTertiary, marginRight: '8px' }}>
          [{new Date(msg.timestamp).toLocaleTimeString()}]
        </span>
        <span style={{ color: token.colorTextSecondary, marginRight: '8px' }}>
          [{msg.type.toUpperCase()}]
        </span>
        {msg.level && (
          <span style={{ color: token.colorTextSecondary, marginRight: '8px' }}>
            [{msg.level}]
          </span>
        )}
        {msg.source && (
          <span style={{ color: token.colorTextSecondary, marginRight: '8px' }}>
            [{msg.source}]
          </span>
        )}
        <span>{msg.content}</span>
      </div>
    )
  }

  // 获取连接状态颜色
  const getStatusColor = (): 'success' | 'processing' | 'error' | 'default' => {
    switch (connectionStatus) {
      case 'connected': return 'success'
      case 'connecting': return 'processing'
      case 'error': return 'error'
      default: return 'default'
    }
  }

  // 获取连接状态文本
  const getStatusText = () => {
    switch (connectionStatus) {
      case 'connected': return '已连接'
      case 'connecting': return '连接中'
      case 'error': return '连接错误'
      default: return '未连接'
    }
  }

  // 导出菜单项
  const exportMenuItems = [
    {
      key: 'txt',
      label: '导出为 TXT',
      onClick: () => exportLogs('txt')
    },
    {
      key: 'json',
      label: '导出为 JSON',
      onClick: () => exportLogs('json')
    },
    {
      key: 'csv',
      label: '导出为 CSV',
      onClick: () => exportLogs('csv')
    }
  ]

  return (
    <div className={`${styles.logViewer} ${isFullscreen ? styles.fullscreen : ''}`}>
      <div style={{ padding: '20px 24px', background: token.colorBgContainer }}>
        {/* 标题与控制栏 */}
        <div style={{ 
          display: 'flex', 
          justifyContent: 'space-between', 
          alignItems: 'center',
          marginBottom: 20,
          flexWrap: 'wrap',
          gap: 12
        }}>
          <Space size="middle">
            <div style={{ 
              width: 4, 
              height: 24, 
              background: token.colorPrimary,
              borderRadius: 2
            }} />
            <div>
              <Text strong style={{ fontSize: 16, display: 'block', lineHeight: 1.2 }}>
                实时日志
              </Text>
              <Space size="small" style={{ marginTop: 4 }}>
                <Tag 
                  color={getStatusColor()} 
                  style={{ 
                    margin: 0,
                    border: 'none',
                    fontSize: 12
                  }}
                >
                  {getStatusText()}
                </Tag>
                <Text type="secondary" style={{ fontSize: 12 }}>
                  执行ID: {executionId.slice(0, 8)}...
                </Text>
              </Space>
            </div>
          </Space>

          {showControls && (
            <Space size="small" wrap>
              {connectionStatus === 'disconnected' ? (
                <Button
                  type="primary"
                  icon={<PlayCircleOutlined />}
                  onClick={connect}
                  size="small"
                >
                  连接
                </Button>
              ) : (
                <Button
                  danger
                  icon={<StopOutlined />}
                  onClick={disconnect}
                  size="small"
                >
                  断开
                </Button>
              )}
              <Button
                icon={<ReloadOutlined />}
                onClick={() => {
                  disconnect()
                  setTimeout(connect, 1000)
                }}
                size="small"
              >
                重连
              </Button>
              <Button
                icon={<ClearOutlined />}
                onClick={clearLogs}
                size="small"
              >
                清除
              </Button>
              {enableExport && (
                <Dropdown menu={{ items: exportMenuItems }} placement="bottomRight">
                  <Button
                    icon={<DownloadOutlined />}
                    size="small"
                  >
                    导出
                  </Button>
                </Dropdown>
              )}
              <Button
                icon={isFullscreen ? <FullscreenExitOutlined /> : <FullscreenOutlined />}
                onClick={() => setIsFullscreen(!isFullscreen)}
                size="small"
              />
            </Space>
          )}
        </div>

        {/* 统计信息 - 更紧凑的网格布局 */}
        <div style={{ 
          marginBottom: 20,
          padding: '16px 20px',
          background: token.colorFillQuaternary,
          borderRadius: 6,
          border: `1px solid ${token.colorBorder}`
        }}>
          <Row gutter={[16, 16]}>
            {statsItems.map(item => (
              <Col key={item.key} xs={12} sm={8} md={4}>
                <div style={{ textAlign: 'center' }}>
                  <div style={{ 
                    fontSize: 24, 
                    fontWeight: 700,
                    color: item.color || token.colorText,
                    lineHeight: 1.2,
                    marginBottom: 4
                  }}>
                    {item.value}
                  </div>
                  <div style={{ 
                    fontSize: 12, 
                    color: token.colorTextTertiary,
                    fontWeight: 500
                  }}>
                    {item.title}
                  </div>
                </div>
              </Col>
            ))}
          </Row>
        </div>

        {/* 过滤控制 - 优化布局 */}
        <div
          style={{
            background: token.colorFillAlter,
            padding: '18px 20px',
            borderRadius: 6,
            border: `1px solid ${token.colorBorder}`,
            marginBottom: 20
          }}
        >
          <div
            style={{
              display: 'flex',
              justifyContent: 'space-between',
              alignItems: 'center',
              marginBottom: 16
            }}
          >
            <Text strong style={{ fontSize: 14 }}>快速筛选</Text>
            <Button
              size="small"
              icon={<ReloadOutlined />}
              onClick={resetBasicFilters}
              type="text"
            >
              重置
            </Button>
          </div>
          <Row gutter={[12, 12]}>
            <Col xs={24} md={12}>
              {enableSearch && (
                <Search
                  placeholder="搜索日志内容或来源..."
                  value={searchText}
                  onChange={(e) => setSearchText(e.target.value)}
                  onSearch={setSearchText}
                  allowClear
                  size="middle"
                />
              )}
            </Col>
            <Col xs={12} md={6}>
              <Select
                mode="multiple"
                placeholder="日志级别"
                value={selectedLevels}
                onChange={setSelectedLevels}
                style={{ width: '100%' }}
                size="middle"
                maxTagCount={1}
              >
                <Option value="DEBUG"><Tag color="default" style={{ margin: 0 }}>DEBUG</Tag></Option>
                <Option value="INFO"><Tag color="blue" style={{ margin: 0 }}>INFO</Tag></Option>
                <Option value="WARNING"><Tag color="orange" style={{ margin: 0 }}>WARNING</Tag></Option>
                <Option value="ERROR"><Tag color="red" style={{ margin: 0 }}>ERROR</Tag></Option>
                <Option value="CRITICAL"><Tag color="magenta" style={{ margin: 0 }}>CRITICAL</Tag></Option>
              </Select>
            </Col>
            <Col xs={12} md={6}>
              <Select
                mode="multiple"
                placeholder="日志类型"
                value={selectedTypes}
                onChange={setSelectedTypes}
                style={{ width: '100%' }}
                size="middle"
                maxTagCount={1}
              >
                <Option value="stdout"><Tag color="green" style={{ margin: 0 }}>标准输出</Tag></Option>
                <Option value="stderr"><Tag color="red" style={{ margin: 0 }}>标准错误</Tag></Option>
              </Select>
            </Col>
            <Col xs={24} style={{ marginTop: 4 }}>
              <Space size="large">
                <Checkbox 
                  checked={isAutoScroll} 
                  onChange={(e) => setIsAutoScroll(e.target.checked)}
                >
                  <Text style={{ fontSize: 13 }}>自动滚动</Text>
                </Checkbox>
                <Checkbox 
                  checked={isPaused} 
                  onChange={(e) => setIsPaused(e.target.checked)}
                >
                  <Text style={{ fontSize: 13 }}>暂停接收</Text>
                </Checkbox>
              </Space>
            </Col>
          </Row>
        </div>

        {/* 日志内容 */}
        {enableVirtualization && displayMessages.length > 100 ? (
          <VirtualLogViewer
            messages={displayMessages}
            height={height}
            searchable={false}
            enableAdvancedFilter={false}
            onClear={clearLogs}
          />
        ) : (
          <div
            ref={logContainerRef}
            className={styles.logContainer}
            style={{
              height: height,
              backgroundColor: token.colorBgContainer,
              color: token.colorText,
              padding: '12px',
              borderRadius: 6,
              overflow: 'auto',
              fontFamily: 'Monaco, Consolas, "Courier New", monospace',
              fontSize: '12px',
              border: `1px solid ${token.colorBorder}`
            }}
          >
            {displayMessages.length === 0 ? (
              <div style={{
                color: token.colorTextSecondary,
                textAlign: 'center',
                paddingTop: '50px',
                fontSize: '14px'
              }}>
                {connectionStatus === 'disconnected'
                  ? '点击连接按钮开始查看日志'
                  : messages.length === 0
                    ? '等待日志消息...'
                    : '没有匹配的日志消息'
                }
              </div>
            ) : (
              displayMessages.map(renderLogMessage)
            )}
          </div>
        )}

        {/* 底部状态栏 - 更简洁 */}
        <div style={{
          marginTop: 16,
          padding: '12px 16px',
          background: token.colorFillQuaternary,
          borderRadius: 6,
          display: 'flex',
          justifyContent: 'space-between',
          alignItems: 'center',
          flexWrap: 'wrap',
          gap: 12
        }}>
          <Text type="secondary" style={{ fontSize: 12 }}>
            显示 <Text strong style={{ color: token.colorTextSecondary }}>{displayMessages.length}</Text> / <Text strong style={{ color: token.colorTextSecondary }}>{messages.length}</Text> 条日志
            {searchText && (
              <span> （搜索: "{searchText}"）</span>
            )}
          </Text>
          <Space size="small">
            <Text type="secondary" style={{ fontSize: 12 }}>连接状态:</Text>
            <Tag 
              color={getStatusColor()} 
              style={{ margin: 0, fontSize: 11 }}
            >
              {getStatusText()}
            </Tag>
          </Space>
        </div>
      </div>
    </div>
  )
}

export default EnhancedLogViewer
