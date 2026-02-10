export interface AppBrandingConfig {
  /** 品牌/平台名称 */
  brandName: string
  /** 页面标题/浏览器标签名 */
  appTitle: string
  /** Logo 图标类型（Ant Design 图标名称） */
  logoIcon: string
  /** Logo 简写（侧边栏收起时显示） */
  logoShort: string
}

const DEFAULT_TITLE = 'AntCode 任务调度平台'

export const APP_BRANDING: AppBrandingConfig = {
  brandName: import.meta.env.VITE_APP_TITLE || DEFAULT_TITLE,
  appTitle: import.meta.env.VITE_APP_TITLE || DEFAULT_TITLE,
  logoIcon: import.meta.env.VITE_APP_LOGO_ICON || 'RocketOutlined',
  logoShort: import.meta.env.VITE_APP_LOGO_SHORT || 'A',
}

export const APP_BRAND_NAME = APP_BRANDING.brandName
export const PLATFORM_TITLE = APP_BRANDING.brandName
export const APP_TITLE = APP_BRANDING.appTitle
export const APP_LOGO_ICON = APP_BRANDING.logoIcon
export const APP_LOGO_SHORT = APP_BRANDING.logoShort
