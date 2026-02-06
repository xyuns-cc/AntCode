export interface AppBrandingConfig {
  /** 品牌/平台名称 */
  brandName: string
  /** 页面标题/浏览器标签名 */
  appTitle: string
  /** Logo 展示名（侧边栏展开时显示） */
  logoText: string
  /** Logo 图标类型（Ant Design 图标名称） */
  logoIcon: string
  /** Logo 简写（侧边栏收起时显示） */
  logoShort: string
  /** Logo 图片 URL（可选，优先于图标） */
  logoUrl?: string
  /** Favicon URL（可选） */
  faviconUrl?: string
}

const DEFAULT_BRAND_NAME = 'AntCode'
const DEFAULT_TITLE = 'AntCode 任务调度平台'
const DEFAULT_LOGO_TEXT = 'AntCode'
const DEFAULT_LOGO_SHORT = DEFAULT_LOGO_TEXT.slice(0, 1)

export const APP_BRANDING: AppBrandingConfig = {
  brandName: import.meta.env.VITE_APP_NAME || DEFAULT_BRAND_NAME,
  appTitle: import.meta.env.VITE_APP_TITLE || DEFAULT_TITLE,
  logoText: import.meta.env.VITE_APP_LOGO_TEXT || DEFAULT_LOGO_TEXT,
  logoIcon: import.meta.env.VITE_APP_LOGO_ICON || 'RocketOutlined',
  logoShort: import.meta.env.VITE_APP_LOGO_SHORT || DEFAULT_LOGO_SHORT,
  logoUrl: import.meta.env.VITE_APP_LOGO_URL || undefined,
  faviconUrl: import.meta.env.VITE_APP_FAVICON_URL || undefined,
}

export const APP_BRAND_NAME = APP_BRANDING.brandName
export const PLATFORM_TITLE = APP_BRANDING.brandName
export const APP_TITLE = APP_BRANDING.appTitle
export const APP_LOGO_TEXT = APP_BRANDING.logoText
export const APP_LOGO_ICON = APP_BRANDING.logoIcon
export const APP_LOGO_SHORT = APP_BRANDING.logoShort
