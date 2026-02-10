/* eslint-disable react-refresh/only-export-components */
import React, { Component, ErrorInfo, ReactNode } from 'react'
import { Button, Result, Typography, Collapse, Space } from 'antd'
import { ReloadOutlined, BugOutlined, HomeOutlined } from '@ant-design/icons'
import CopyableTooltip from './CopyableTooltip'
import Logger from '@/utils/logger'

const { Paragraph, Text } = Typography

interface Props {
  children: ReactNode
  fallback?: ReactNode
  onError?: (error: Error, errorInfo: ErrorInfo) => void
}

interface State {
  hasError: boolean
  error: Error | null
  errorInfo: ErrorInfo | null
  errorCount: number
}

class ErrorBoundary extends Component<Props, State> {
  constructor(props: Props) {
    super(props)
    this.state = {
      hasError: false,
      error: null,
      errorInfo: null,
      errorCount: 0
    }
  }

  static getDerivedStateFromError(error: Error): State {
    return {
      hasError: true,
      error,
      errorInfo: null,
      errorCount: 0
    }
  }

  componentDidCatch(error: Error, errorInfo: ErrorInfo) {
    // 记录错误信息
    Logger.error('ErrorBoundary caught an error:', error, errorInfo)

    // 更新状态
    this.setState(prevState => ({
      errorInfo,
      errorCount: prevState.errorCount + 1
    }))

    // 调用错误回调
    if (this.props.onError) {
      this.props.onError(error, errorInfo)
    }

    // 生产环境下发送错误报告
    if (process.env.NODE_ENV === 'production') {
      this.reportError(error, errorInfo)
    }
  }

  reportError = (error: Error, errorInfo: ErrorInfo) => {
    // 这里可以集成错误监控服务，如 Sentry
    const errorReport = {
      message: error.message,
      stack: error.stack,
      componentStack: errorInfo.componentStack,
      timestamp: new Date().toISOString(),
      userAgent: navigator.userAgent,
      url: window.location.href
    }

    // 发送到错误监控服务
    Logger.info('Error report:', errorReport)
    
    // 可通过 API 发送到后端
    // apiClient.post('/api/v1/errors', errorReport)
  }

  handleReset = () => {
    this.setState({
      hasError: false,
      error: null,
      errorInfo: null,
      errorCount: 0
    })
  }

  handleReload = () => {
    window.location.reload()
  }

  handleGoHome = () => {
    window.location.href = '/'
  }

  render() {
    const { hasError, error, errorInfo, errorCount } = this.state
    const { children, fallback } = this.props

    if (hasError) {
      // 如果提供了自定义的 fallback 组件
      if (fallback) {
        return <>{fallback}</>
      }

      // 默认错误页面
      return (
        <div style={{ 
          minHeight: '100vh', 
          display: 'flex', 
          alignItems: 'center', 
          justifyContent: 'center',
          padding: '20px'
        }}>
          <Result
            status="error"
            title="页面出现了一些问题"
            subTitle="很抱歉，页面遇到了意外错误。我们已经记录了这个问题，会尽快修复。"
            extra={[
              <Space key="actions" size="middle">
                <Button 
                  type="primary" 
                  icon={<ReloadOutlined />}
                  onClick={this.handleReload}
                >
                  刷新页面
                </Button>
                <Button 
                  icon={<HomeOutlined />}
                  onClick={this.handleGoHome}
                >
                  返回首页
                </Button>
                {errorCount < 3 && (
                  <Button 
                    type="default"
                    onClick={this.handleReset}
                  >
                    重试
                  </Button>
                )}
              </Space>
            ]}
          >
            {/* 开发环境显示详细错误信息 */}
            {process.env.NODE_ENV === 'development' && error && (
              <div style={{ marginTop: '20px', textAlign: 'left' }}>
                <Collapse 
                  ghost 
                  expandIcon={({ isActive }) => (
                    <BugOutlined rotate={isActive ? 90 : 0} />
                  )}
                  items={[{
                    key: '1',
                    label: '错误详情（仅开发环境可见）',
                    children: (
                      <Space direction="vertical" style={{ width: '100%' }}>
                        <div>
                          <Text strong>错误信息：</Text>
                          <CopyableTooltip text={error.message}>
                            <code style={{ cursor: 'pointer', display: 'block' }}>
                              {error.message}
                            </code>
                          </CopyableTooltip>
                        </div>
                        
                        {error.stack && (
                          <div>
                            <Text strong>错误堆栈：</Text>
                            <Paragraph>
                              <pre style={{ 
                                fontSize: '12px', 
                                overflow: 'auto',
                                maxHeight: '200px',
                                padding: '10px',
                                backgroundColor: 'var(--ant-color-fill-tertiary)',
                                borderRadius: '4px'
                              }}>
                                {error.stack}
                              </pre>
                            </Paragraph>
                          </div>
                        )}

                        {errorInfo?.componentStack && (
                          <div>
                            <Text strong>组件堆栈：</Text>
                            <Paragraph>
                              <pre style={{ 
                                fontSize: '12px', 
                                overflow: 'auto',
                                maxHeight: '200px',
                                padding: '10px',
                                backgroundColor: 'var(--ant-color-fill-tertiary)',
                                borderRadius: '4px'
                              }}>
                                {errorInfo.componentStack}
                              </pre>
                            </Paragraph>
                          </div>
                        )}

                        <div>
                          <Text type="secondary">
                            错误次数：{errorCount} | 
                            时间：{new Date().toLocaleString()}
                          </Text>
                        </div>
                      </Space>
                    )
                  }]}
                />
              </div>
            )}
          </Result>
        </div>
      )
    }

    return children
  }
}

// 异步组件错误边界
export const AsyncBoundary: React.FC<{ children: ReactNode }> = ({ children }) => {
  return (
    <ErrorBoundary
      fallback={
        <Result
          status="warning"
          title="组件加载失败"
          subTitle="该组件暂时无法加载，请稍后再试。"
          extra={
            <Button type="primary" onClick={() => window.location.reload()}>
              刷新页面
            </Button>
          }
        />
      }
    >
      {children}
    </ErrorBoundary>
  )
}

// 高阶组件：为组件添加错误边界
export function withErrorBoundary<P extends object>(
  Component: React.ComponentType<P>,
  fallback?: ReactNode
): React.FC<P> {
  return (props: P) => (
    <ErrorBoundary fallback={fallback}>
      <Component {...props} />
    </ErrorBoundary>
  )
}

export default ErrorBoundary
