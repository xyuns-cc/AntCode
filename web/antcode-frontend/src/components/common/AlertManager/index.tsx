import React, { createContext, useContext, useState, useCallback } from 'react'
import { Alert, Space } from 'antd'
import './styles.css'

export interface AlertItem {
  id: string
  message: string
  description?: string
  type: 'success' | 'info' | 'warning' | 'error'
  duration?: number // 自动关闭时间（毫秒），0为不自动关闭
  closable?: boolean
  showIcon?: boolean
}

interface AlertContextType {
  showAlert: (item: Omit<AlertItem, 'id'>) => string
  hideAlert: (id: string) => void
  clearAll: () => void
}

const AlertContext = createContext<AlertContextType | null>(null)

export const AlertProvider: React.FC<{ children: React.ReactNode }> = ({ children }) => {
  const [alerts, setAlerts] = useState<AlertItem[]>([])

  const showAlert = useCallback((item: Omit<AlertItem, 'id'>): string => {
    const id = Date.now().toString() + Math.random().toString(36).substr(2, 9)
    const newAlert: AlertItem = {
      id,
      closable: true,
      showIcon: true,
      duration: 4500, // 默认4.5秒
      ...item,
    }

    setAlerts(prev => [...prev, newAlert])

    // 如果设置了自动关闭时间且大于0，则自动关闭
    if (newAlert.duration && newAlert.duration > 0) {
      setTimeout(() => {
        hideAlert(id)
      }, newAlert.duration)
    }

    return id
  }, [])

  const hideAlert = useCallback((id: string) => {
    setAlerts(prev => prev.filter(alert => alert.id !== id))
  }, [])

  const clearAll = useCallback(() => {
    setAlerts([])
  }, [])

  return (
    <AlertContext.Provider value={{ showAlert, hideAlert, clearAll }}>
      {children}
      <div className="alert-manager">
        <Space direction="vertical" size="small">
          {alerts.map(alert => (
            <Alert
              key={alert.id}
              message={alert.message}
              description={alert.description}
              type={alert.type}
              closable={alert.closable}
              showIcon={alert.showIcon}
              onClose={() => hideAlert(alert.id)}
              className="alert-item"
            />
          ))}
        </Space>
      </div>
    </AlertContext.Provider>
  )
}

export const useAlert = () => {
  const context = useContext(AlertContext)
  if (!context) {
    throw new Error('useAlert must be used within AlertProvider')
  }
  return context
}

// 全局Alert函数
let globalAlertFunction: AlertContextType | null = null

export const setGlobalAlert = (alertFn: AlertContextType) => {
  globalAlertFunction = alertFn
}

export const globalAlert = {
  success: (message: string, description?: string, duration = 4500) => {
    return globalAlertFunction?.showAlert({ message, description, type: 'success', duration }) || ''
  },
  info: (message: string, description?: string, duration = 4500) => {
    return globalAlertFunction?.showAlert({ message, description, type: 'info', duration }) || ''
  },
  warning: (message: string, description?: string, duration = 4500) => {
    return globalAlertFunction?.showAlert({ message, description, type: 'warning', duration }) || ''
  },
  error: (message: string, description?: string, duration = 0) => {
    return globalAlertFunction?.showAlert({ message, description, type: 'error', duration }) || ''
  },
  show: (type: 'success' | 'info' | 'warning' | 'error', message: string, description?: string, duration?: number) => {
    return globalAlertFunction?.showAlert({ message, description, type, duration }) || ''
  }
}