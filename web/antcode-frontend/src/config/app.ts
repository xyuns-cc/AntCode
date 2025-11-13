export interface AppBrandingConfig {
  /**
   * 统一的品牌/平台名称，例如 'AntCode 任务调度平台'
   */
  brandName: string
  /**
   * 页面标题/浏览器标签名
   */
  appTitle: string
}

const DEFAULT_TITLE = 'AntCode 任务调度平台'

export const APP_BRANDING: AppBrandingConfig = {
  brandName: import.meta.env.VITE_APP_TITLE || DEFAULT_TITLE,
  appTitle: import.meta.env.VITE_APP_TITLE || DEFAULT_TITLE,
}

export const APP_BRAND_NAME = APP_BRANDING.brandName
export const PLATFORM_TITLE = APP_BRANDING.brandName
export const APP_TITLE = APP_BRANDING.appTitle
