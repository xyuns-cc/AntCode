/* eslint-disable react-refresh/only-export-components */
import React, { lazy, Suspense, ComponentType } from 'react'
import { Spin } from 'antd'
import { LoadingOutlined } from '@ant-design/icons'

// 全局加载组件
const PageLoading: React.FC = () => (
  <div style={{
    height: '100vh',
    display: 'flex',
    alignItems: 'center',
    justifyContent: 'center'
  }}>
    <Spin 
      indicator={<LoadingOutlined style={{ fontSize: 24 }} spin />} 
      tip="加载中..."
    >
      <div style={{ width: '200px', height: '100px' }} />
    </Spin>
  </div>
)

// 组件级加载
const ComponentLoading: React.FC = () => (
  <div style={{
    padding: '20px',
    textAlign: 'center'
  }}>
    <Spin />
  </div>
)

// 懒加载包装器
export function lazyLoad<Props extends object>(
  importFunc: () => Promise<{ default: ComponentType<Props> }>,
  fallback?: React.ReactNode
): React.FC<Props> {
  const LazyComponent = lazy(importFunc)

  return (props: Props) => (
    <Suspense fallback={fallback || <PageLoading />}>
      <LazyComponent {...props} />
    </Suspense>
  )
}

// 带错误边界的懒加载
export function lazyLoadWithErrorBoundary<Props extends object>(
  importFunc: () => Promise<{ default: ComponentType<Props> }>,
  fallback?: React.ReactNode
): React.FC<Props> {
  const LazyComponent = lazy(importFunc)

  return (props: Props) => (
    <ErrorBoundary>
      <Suspense fallback={fallback || <PageLoading />}>
        <LazyComponent {...props} />
      </Suspense>
    </ErrorBoundary>
  )
}

// 预加载函数
export function preloadComponent(
  importFunc: () => Promise<{ default: ComponentType<Record<string, unknown>> }>
): void {
  importFunc()
}

// 错误边界组件
class ErrorBoundary extends React.Component<
  { children: React.ReactNode },
  { hasError: boolean }
> {
  constructor(props: { children: React.ReactNode }) {
    super(props)
    this.state = { hasError: false }
  }

  static getDerivedStateFromError() {
    return { hasError: true }
  }

  componentDidCatch(_error: Error, _errorInfo: React.ErrorInfo) {
    // 错误已被捕获，可以在这里记录到日志服务
  }

  render() {
    if (this.state.hasError) {
      return (
        <div style={{ padding: '20px', textAlign: 'center' }}>
          <h3>组件加载失败</h3>
          <p>请刷新页面重试</p>
        </div>
      )
    }

    return this.props.children
  }
}

export { PageLoading, ComponentLoading }
