import type React from 'react'
import { Navigate } from 'react-router-dom'
import { Result, Button, theme } from 'antd'
import { LockOutlined } from '@ant-design/icons'
import { useAuth } from '@/hooks/useAuth'

interface SuperAdminRouteProps {
  children: React.ReactNode
  fallback?: React.ReactNode
}

/**
 * 超级管理员路由保护组件
 * 只允许 role 为 'super_admin' 的超级管理员访问
 */
const SuperAdminRoute: React.FC<SuperAdminRouteProps> = ({
  children,
  fallback
}) => {
  const { token } = theme.useToken()
  const { user, isAuthenticated } = useAuth()

  // 如果用户未认证，重定向到登录页面
  if (!isAuthenticated) {
    return <Navigate to="/login" replace />
  }

  // 基于 role 字段判断超级管理员，兼容旧的 is_super_admin 字段
  if (user?.role !== 'super_admin' && !user?.is_super_admin) {
    if (fallback) {
      return <>{fallback}</>
    }

    return (
      <Result
        status="403"
        title="403"
        subTitle="抱歉，此功能仅限超级管理员（admin用户）访问。"
        icon={<LockOutlined style={{ color: token.colorError, fontSize: 72 }} />}
        extra={
          <Button type="primary" onClick={() => window.history.back()}>
            返回
          </Button>
        }
      />
    )
  }

  // 超级管理员可以正常访问
  return <>{children}</>
}

export default SuperAdminRoute

