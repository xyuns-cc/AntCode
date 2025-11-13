import React, { useEffect } from 'react'
import { useAlert, setGlobalAlert, globalAlert } from '@/components/common/AlertManager'

const AlertInitializer: React.FC = () => {
  const alertManager = useAlert()

  useEffect(() => {
    // 设置全局Alert管理器
    setGlobalAlert(alertManager)
    
    // 暴露全局函数
    ;(window as any).__globalAlert = globalAlert
    ;(window as any).__notify = globalAlert.show // 兼容旧API
    
    // 清理函数
    return () => {
      ;(window as any).__globalAlert = undefined
      ;(window as any).__notify = undefined
    }
  }, [alertManager])

  return null
}

export default AlertInitializer