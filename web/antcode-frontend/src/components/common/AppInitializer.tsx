import { useEffect } from 'react'
import { App } from 'antd'
import { setMessageInstances } from '@/hooks/useMessage'
import { useBrandingStore } from '@/stores/brandingStore'

/** Initializes global message/notification/modal instances from App context */
const AppInitializer: React.FC = () => {
  const { message, notification, modal } = App.useApp()
  const branding = useBrandingStore((state) => state.branding)
  const fetchBranding = useBrandingStore((state) => state.fetchBranding)

  useEffect(() => {
    setMessageInstances(message, notification, modal)
  }, [message, notification, modal])

  useEffect(() => {
    fetchBranding()
  }, [fetchBranding])

  useEffect(() => {
    if (branding.appTitle) {
      document.title = branding.appTitle
    }
  }, [branding.appTitle])

  useEffect(() => {
    if (!branding.faviconUrl) return

    const existingLink = document.querySelector("link[rel='icon']") as HTMLLinkElement | null
    if (existingLink) {
      existingLink.href = branding.faviconUrl
      return
    }

    const link = document.createElement('link')
    link.rel = 'icon'
    link.href = branding.faviconUrl
    document.head.appendChild(link)
  }, [branding.faviconUrl])

  return null
}

export default AppInitializer
