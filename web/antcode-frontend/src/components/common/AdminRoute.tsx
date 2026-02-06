import type React from 'react'
import { Navigate } from 'react-router-dom'
import { Result, Button, theme } from 'antd'
import { TeamOutlined } from '@ant-design/icons'
import { useAuth } from '@/hooks/useAuth'

interface AdminRouteProps {
  children: React.ReactNode
  fallback?: React.ReactNode
}

const AdminRoute: React.FC<AdminRouteProps> = ({ 
  children, 
  fallback 
}) => {
  const { token } = theme.useToken()
  const { user, isAuthenticated } = useAuth()

  // 如果用户未认证，重定向到登录页面
  if (!isAuthenticated) {
    return <Navigate to="/login" replace />
  }

  // 如果用户不是管理员，显示权限不足页面
  if (!user?.is_admin) {
    if (fallback) {
      return <>{fallback}</>
    }

    return (
      <Result
        status="403"
        title="403"
        subTitle="抱歉，您没有权限访问此页面。"
        icon={<TeamOutlined style={{ color: token.colorError }} />}
        extra={
          <Button type="primary" onClick={() => window.history.back()}>
            返回
          </Button>
        }
      />
    )
  }

  // 管理员可以正常访问
  return <>{children}</>
}

export default AdminRoute
