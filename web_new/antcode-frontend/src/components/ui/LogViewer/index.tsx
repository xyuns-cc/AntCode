import React, { useRef, useState, useCallback } from 'react'
import { Card, Button, Space, theme } from 'antd'
import { ClearOutlined } from '@ant-design/icons'

// 用于生成唯一ID的计数器
let idCounter = 0

// 生成唯一ID的函数
const generateUniqueId = (): string => {
  idCounter += 1
  return `${Date.now()}-${idCounter}-${Math.random().toString(36).substr(2, 9)}`
}

interface LogMessage {
  id: string
  type: 'info' | 'error' | 'warning' | 'success'
  content: string
  timestamp: string
}

interface LogViewerProps {
  height?: number
  showControls?: boolean
}

const LogViewer: React.FC<LogViewerProps> = ({
  height = 400,
  showControls = true
}) => {
  const { token } = theme.useToken()
  const [logs, setLogs] = useState<LogMessage[]>([])
  const logContainerRef = useRef<HTMLDivElement>(null)

  // 添加日志消息
  const addLogMessage = useCallback((logMessage: LogMessage) => {
    setLogs(prev => {
      const newLogs = [...prev, logMessage]
      // 限制日志数量，避免内存泄漏
      if (newLogs.length > 1000) {
        return newLogs.slice(-500)
      }
      return newLogs
    })

    // 自动滚动到底部
    setTimeout(() => {
      if (logContainerRef.current) {
        logContainerRef.current.scrollTop = logContainerRef.current.scrollHeight
      }
    }, 10)
  }, [])



  // 清除日志
  const clearLogs = useCallback(() => {
    setLogs([])
    addLogMessage({
      id: generateUniqueId(),
      type: 'info',
      content: '[清除] 日志已清除',
      timestamp: new Date().toISOString()
    })
  }, [addLogMessage])

  // 渲染日志消息
  const renderLogMessage = (msg: LogMessage) => {
    let color = token.colorText
    const backgroundColor = 'transparent'

    if (msg.type === 'error') {
      color = token.colorError
    } else if (msg.type === 'info') {
      color = token.colorInfo
    } else if (msg.type === 'warning') {
      color = token.colorWarning
    } else if (msg.type === 'success') {
      color = token.colorSuccess
    }

    return (
      <div
        key={msg.id}
        style={{
          padding: '4px 8px',
          margin: '1px 0',
          fontFamily: 'Monaco, Consolas, "Courier New", monospace',
          fontSize: '12px',
          color,
          backgroundColor,
          borderLeft: `3px solid ${color}`,
          wordBreak: 'break-all'
        }}
      >
        <span style={{ color: token.colorTextTertiary, marginRight: '8px' }}>
          {new Date(msg.timestamp).toLocaleTimeString()}
        </span>
        {msg.content}
      </div>
    )
  }

  return (
    <Card
      title={
        <Space>
          <span>日志查看器</span>
          <span>总计: {logs.length} 条消息</span>
        </Space>
      }
      extra={
        showControls && (
          <Space>
            <Button
              icon={<ClearOutlined />}
              onClick={clearLogs}
            >
              清除
            </Button>
          </Space>
        )
      }
    >
      <div
        ref={logContainerRef}
        style={{
          height: height,
          backgroundColor: token.colorBgContainer,
          color: token.colorText,
          padding: '12px',
          borderRadius: token.borderRadius,
          overflow: 'auto',
          fontFamily: 'Monaco, Consolas, "Courier New", monospace',
          fontSize: '12px',
          border: `1px solid ${token.colorBorder}`
        }}
      >
        {logs.length === 0 ? (
          <div style={{ color: token.colorTextTertiary, textAlign: 'center', paddingTop: '50px' }}>
            暂无日志消息
          </div>
        ) : (
          logs.map(renderLogMessage)
        )}
      </div>
    </Card>
  )
}

export default LogViewer
