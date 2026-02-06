/**
 * 主题工具函数
 * 管理CSS变量和主题切换
 */

export type ThemeMode = 'light' | 'dark' | 'system'

// CSS 变量名映射
export const cssVariables = {
  // 颜色变量
  primary: '--primary-color',
  primaryHover: '--primary-color-hover',
  primaryActive: '--primary-color-active',
  primaryLight: '--primary-color-light',
  
  success: '--success-color',
  successHover: '--success-color-hover',
  successActive: '--success-color-active',
  successLight: '--success-color-light',
  
  warning: '--warning-color',
  warningHover: '--warning-color-hover',
  warningActive: '--warning-color-active',
  warningLight: '--warning-color-light',
  
  error: '--error-color',
  errorHover: '--error-color-hover',
  errorActive: '--error-color-active',
  errorLight: '--error-color-light',
  
  info: '--info-color',
  infoHover: '--info-color-hover',
  infoActive: '--info-color-active',
  infoLight: '--info-color-light',
  
  // 中性色
  textColor: '--text-color',
  textColorSecondary: '--text-color-secondary',
  textColorDisabled: '--text-color-disabled',
  borderColor: '--border-color',
  borderColorLight: '--border-color-light',
  backgroundColor: '--background-color',
  backgroundColorLight: '--background-color-light',
  
  // 主题专用变量
  bgColor: '--bg-color',
  bgSecondary: '--bg-secondary',
  bgTertiary: '--bg-tertiary',
  shadowColor: '--shadow-color',
  
  // 阴影
  shadow1: '--shadow-1',
  shadow2: '--shadow-2',
  shadow3: '--shadow-3',
  
  // 圆角
  borderRadiusSm: '--border-radius-sm',
  borderRadius: '--border-radius',
  borderRadiusLg: '--border-radius-lg',
  
  // 间距
  spacingXs: '--spacing-xs',
  spacingSm: '--spacing-sm',
  spacingMd: '--spacing-md',
  spacingLg: '--spacing-lg',
  spacingXl: '--spacing-xl',
  
  // 字体大小
  fontSizeSm: '--font-size-sm',
  fontSizeBase: '--font-size-base',
  fontSizeLg: '--font-size-lg',
  fontSizeXl: '--font-size-xl',
  fontSizeXxl: '--font-size-xxl',
  
  // 行高
  lineHeightSm: '--line-height-sm',
  lineHeightBase: '--line-height-base',
  lineHeightLg: '--line-height-lg',
  
  // 布局尺寸
  headerHeight: '--header-height',
  sidebarWidth: '--sidebar-width',
  sidebarCollapsedWidth: '--sidebar-collapsed-width',
  
  // 动画时长
  animationDurationSlow: '--animation-duration-slow',
  animationDurationBase: '--animation-duration-base',
  animationDurationFast: '--animation-duration-fast',
} as const

// 获取CSS变量值
export function getCSSVariable(variable: string): string {
  return getComputedStyle(document.documentElement).getPropertyValue(variable)
}

// 设置CSS变量值
export function setCSSVariable(variable: string, value: string): void {
  document.documentElement.style.setProperty(variable, value)
}

// 批量设置CSS变量
export function setCSSVariables(variables: Record<string, string>): void {
  Object.entries(variables).forEach(([key, value]) => {
    setCSSVariable(key, value)
  })
}

// 获取当前主题模式
export function getCurrentTheme(): ThemeMode {
  const stored = localStorage.getItem('theme-mode')
  if (stored === 'light' || stored === 'dark' || stored === 'system') {
    return stored
  }
  return 'system'
}

// 设置主题模式
export function setThemeMode(mode: ThemeMode): void {
  localStorage.setItem('theme-mode', mode)
  applyTheme(mode)
}

// 应用主题
export function applyTheme(mode: ThemeMode): void {
  const isDark = mode === 'dark' || (mode === 'system' && window.matchMedia('(prefers-color-scheme: dark)').matches)
  
  document.body.className = isDark ? 'dark-theme' : 'light-theme'
  
  // 更新meta主题颜色
  const themeColorMeta = document.querySelector('meta[name="theme-color"]')
  if (themeColorMeta) {
    themeColorMeta.setAttribute('content', isDark ? '#141414' : '#ffffff')
  }
}

// 初始化主题
export function initTheme(): (() => void) | undefined {
  const theme = getCurrentTheme()
  applyTheme(theme)
  
  // 监听系统主题变化
  if (theme === 'system') {
    const mediaQuery = window.matchMedia('(prefers-color-scheme: dark)')
    const handleChange = () => applyTheme('system')
    mediaQuery.addEventListener('change', handleChange)
    
    return () => mediaQuery.removeEventListener('change', handleChange)
  }

  return undefined
}

// 切换主题
export function toggleTheme(): ThemeMode {
  const current = getCurrentTheme()
  const next: ThemeMode = current === 'light' ? 'dark' : current === 'dark' ? 'system' : 'light'
  setThemeMode(next)
  return next
}

// 主题工具类
export class ThemeManager {
  private listeners: Array<(theme: ThemeMode) => void> = []
  private mediaQuery: MediaQueryList | null = null
  
  constructor() {
    this.initTheme()
  }
  
  // 初始化主题
  initTheme(): void {
    const cleanup = initTheme()
    if (cleanup) {
      this.setupMediaQueryListener()
    }
  }
  
  // 设置媒体查询监听
  private setupMediaQueryListener(): void {
    this.mediaQuery = window.matchMedia('(prefers-color-scheme: dark)')
    const handleChange = () => {
      if (getCurrentTheme() === 'system') {
        this.notifyListeners(getCurrentTheme())
      }
    }
    this.mediaQuery.addEventListener('change', handleChange)
  }
  
  // 获取当前主题
  getCurrentTheme(): ThemeMode {
    return getCurrentTheme()
  }
  
  // 设置主题
  setTheme(mode: ThemeMode): void {
    setThemeMode(mode)
    this.notifyListeners(mode)
  }
  
  // 切换主题
  toggleTheme(): ThemeMode {
    const next = toggleTheme()
    this.notifyListeners(next)
    return next
  }
  
  // 添加主题变化监听器
  addListener(listener: (theme: ThemeMode) => void): () => void {
    this.listeners.push(listener)
    return () => {
      const index = this.listeners.indexOf(listener)
      if (index > -1) {
        this.listeners.splice(index, 1)
      }
    }
  }
  
  // 通知监听器
  private notifyListeners(theme: ThemeMode): void {
    this.listeners.forEach(listener => listener(theme))
  }
  
  // 销毁
  destroy(): void {
    this.listeners.length = 0
    if (this.mediaQuery) {
      this.mediaQuery.removeEventListener('change', () => {})
    }
  }
}

// 单例主题管理器
export const themeManager = new ThemeManager()

export default themeManager
