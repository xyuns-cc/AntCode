import React, { useState, useEffect, memo } from 'react'
import { Alert, Button, Space, Typography, Collapse } from 'antd'
import { 
  ExclamationCircleOutlined, 
  ReloadOutlined, 
  CloseOutlined,
  BugOutlined,
  InfoCircleOutlined
} from '@ant-design/icons'
import { ApiError, ErrorLevel } from '@/utils/errorHandler'

const { Text, Paragraph } = Typography
const { Panel } = Collapse

interface ErrorAlertProps {
  error: ApiError | Error | string | null
  level?: ErrorLevel
  showDetails?: boolean
  showRetry?: boolean
  showClose?: boolean
  onRetry?: () => void
  onClose?: () => void
  className?: string
  style?: React.CSSProperties
}

const ErrorAlert: React.FC<ErrorAlertProps> = memo(({
  error,
  level = ErrorLevel.ERROR,
  showDetails = false,
  showRetry = false,
  showClose = true,
  onRetry,
  onClose,
  className,
  style
}) => {
  const [visible, setVisible] = useState(!!error)
  const [collapsed, setCollapsed] = useState(true)

  useEffect(() => {
    setVisible(!!error)
  }, [error])

  if (!error || !visible) return null

  // 解析错误信息
  const parseError = (err: ApiError | Error | string): {
    message: string
    code?: string
    details?: any
    stack?: string
  } => {
    if (typeof err === 'string') {
      return { message: err }
    }
    
    if (err instanceof Error) {
      return {
        message: err.message,
        code: err.name,
        stack: err.stack
      }
    }
    
    // ApiError
    return {
      message: err.message,
      code: err.code,
      details: err.details
    }
  }

  const errorInfo = parseError(error)

  // 获取Alert类型
  const getAlertType = (): 'success' | 'info' | 'warning' | 'error' => {
    switch (level) {
      case ErrorLevel.INFO:
        return 'info'
      case ErrorLevel.WARNING:
        return 'warning'
      case ErrorLevel.ERROR:
      case ErrorLevel.CRITICAL:
        return 'error'
      default:
        return 'error'
    }
  }

  // 获取图标
  const getIcon = () => {
    switch (level) {
      case ErrorLevel.INFO:
        return <InfoCircleOutlined />
      case ErrorLevel.WARNING:
        return <ExclamationCircleOutlined />
      case ErrorLevel.ERROR:
      case ErrorLevel.CRITICAL:
        return <BugOutlined />
      default:
        return <ExclamationCircleOutlined />
    }
  }

  // 处理关闭
  const handleClose = () => {
    setVisible(false)
    onClose?.()
  }

  // 处理重试
  const handleRetry = () => {
    onRetry?.()
  }

  // 渲染操作按钮
  const renderActions = () => {
    const actions = []

    if (showRetry && onRetry) {
      actions.push(
        <Button
          key="retry"
          type="primary"
          size="small"
          icon={<ReloadOutlined />}
          onClick={handleRetry}
        >
          重试
        </Button>
      )
    }

    if (showClose) {
      actions.push(
        <Button
          key="close"
          type="text"
          size="small"
          icon={<CloseOutlined />}
          onClick={handleClose}
        >
          关闭
        </Button>
      )
    }

    return actions.length > 0 ? <Space>{actions}</Space> : null
  }

  // 渲染错误详情
  const renderDetails = () => {
    if (!showDetails) return null

    const hasDetails = errorInfo.code || errorInfo.details || errorInfo.stack

    if (!hasDetails) return null

    return (
      <div style={{ marginTop: 12 }}>
        <Collapse 
          ghost 
          size="small"
          activeKey={collapsed ? [] : ['details']}
          onChange={(keys) => setCollapsed(keys.length === 0)}
        >
          <Panel 
            header={
              <Text type="secondary" style={{ fontSize: '12px' }}>
                查看详细信息
              </Text>
            } 
            key="details"
          >
            <div style={{ fontSize: '12px' }}>
              {errorInfo.code && (
                <div style={{ marginBottom: 8 }}>
                  <Text strong>错误代码: </Text>
                  <Text code>{errorInfo.code}</Text>
                </div>
              )}

              {errorInfo.details && (
                <div style={{ marginBottom: 8 }}>
                  <Text strong>详细信息: </Text>
                  <Paragraph>
                    <pre style={{
                      fontSize: '11px',
                      backgroundColor: '#f5f5f5',
                      padding: '8px',
                      borderRadius: '4px',
                      margin: 0,
                      maxHeight: '150px',
                      overflow: 'auto'
                    }}>
                      {typeof errorInfo.details === 'string' 
                        ? errorInfo.details 
                        : JSON.stringify(errorInfo.details, null, 2)
                      }
                    </pre>
                  </Paragraph>
                </div>
              )}

              {errorInfo.stack && (
                <div>
                  <Text strong>堆栈信息: </Text>
                  <Paragraph>
                    <pre style={{
                      fontSize: '11px',
                      backgroundColor: '#f5f5f5',
                      padding: '8px',
                      borderRadius: '4px',
                      margin: 0,
                      maxHeight: '150px',
                      overflow: 'auto'
                    }}>
                      {errorInfo.stack}
                    </pre>
                  </Paragraph>
                </div>
              )}
            </div>
          </Panel>
        </Collapse>
      </div>
    )
  }

  return (
    <Alert
      type={getAlertType()}
      message={errorInfo.message}
      icon={getIcon()}
      action={renderActions()}
      closable={false} // 使用自定义关闭按钮
      className={className}
      style={style}
      description={renderDetails()}
    />
  )
})

ErrorAlert.displayName = 'ErrorAlert'

export default ErrorAlert

// 便捷组件
export const NetworkErrorAlert: React.FC<Omit<ErrorAlertProps, 'error' | 'level'>> = (props) => (
  <ErrorAlert
    error="网络连接失败，请检查网络设置后重试"
    level={ErrorLevel.ERROR}
    showRetry
    {...props}
  />
)

export const AuthErrorAlert: React.FC<Omit<ErrorAlertProps, 'error' | 'level'>> = (props) => (
  <ErrorAlert
    error="登录已过期，请重新登录"
    level={ErrorLevel.WARNING}
    {...props}
  />
)

export const ValidationErrorAlert: React.FC<Omit<ErrorAlertProps, 'level'> & { errors: string[] }> = ({ 
  errors, 
  ...props 
}) => (
  <ErrorAlert
    error={errors.join('; ')}
    level={ErrorLevel.WARNING}
    {...props}
  />
)

export const ServerErrorAlert: React.FC<Omit<ErrorAlertProps, 'error' | 'level'>> = (props) => (
  <ErrorAlert
    error="服务器暂时不可用，请稍后重试"
    level={ErrorLevel.ERROR}
    showRetry
    {...props}
  />
)
