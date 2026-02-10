import { useEffect } from 'react'
import { App } from 'antd'
import { setMessageInstances } from '@/hooks/useMessage'

/** Initializes global message/notification/modal instances from App context */
const AppInitializer: React.FC = () => {
  const { message, notification, modal } = App.useApp()

  useEffect(() => {
    setMessageInstances(message, notification, modal)
  }, [message, notification, modal])

  return null
}

export default AppInitializer
