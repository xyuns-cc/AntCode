import type React from 'react'
import { useEffect } from 'react'
import { useAuth } from '@/hooks/useAuth'
import { AuthHandler } from '@/utils/authHandler'
import { TokenManager } from '@/services/api'
import Logger from '@/utils/logger'

interface AuthGuardProps {
  children: React.ReactNode
}

/**
 * 认证守卫组件
 * 监听认证状态变化，处理认证失败的情况
 */
const AuthGuard: React.FC<AuthGuardProps> = ({ children }) => {
  const { isAuthenticated } = useAuth()

  useEffect(() => {
    // 监听页面可见性变化，当页面重新可见时检查认证状态
    const handleVisibilityChange = () => {
      if (document.visibilityState === 'visible' && isAuthenticated) {
        // 页面重新可见时，检查token是否仍然有效
        const token = TokenManager.getAccessToken()
        if (!token) {
          Logger.info('页面重新可见时发现token已失效')
          AuthHandler.handleAuthFailure()
        }
      }
    }

    // 监听网络状态变化
    const handleOnline = () => {
      if (isAuthenticated) {
        Logger.info('网络重新连接，检查认证状态')
      }
    }

    // 添加事件监听器
    document.addEventListener('visibilitychange', handleVisibilityChange)
    window.addEventListener('online', handleOnline)

    // 清理函数
    return () => {
      document.removeEventListener('visibilitychange', handleVisibilityChange)
      window.removeEventListener('online', handleOnline)
    }
  }, [isAuthenticated])

  // 定期检查token有效性
  useEffect(() => {
    if (!isAuthenticated) return

    const checkTokenValidity = () => {
      const token = TokenManager.getAccessToken()
      if (!token) {
        Logger.warn('定期检查发现token已失效')
        AuthHandler.handleAuthFailure()
        return
      }

      if (TokenManager.isTokenExpired(token)) {
        Logger.warn('定期检查发现token已过期')
        AuthHandler.handleAuthFailure()
      }
    }

    // 每5分钟检查一次token有效性
    const interval = setInterval(checkTokenValidity, 5 * 60 * 1000)

    return () => clearInterval(interval)
  }, [isAuthenticated])

  return <>{children}</>
}

export default AuthGuard
