import type React from 'react'
import { createContext, useContext, useEffect, useMemo, useCallback, useState } from 'react'
import { theme, type ThemeConfig } from 'antd'

export type ThemeMode = 'light' | 'dark' | 'system'

interface ThemeContextType {
  themeMode: ThemeMode
  isDark: boolean
  setThemeMode: (mode: ThemeMode) => void
  antdTheme: ThemeConfig
  token: ReturnType<typeof theme.useToken>['token'] | null
}

const ThemeContext = createContext<ThemeContextType | undefined>(undefined)

// Fast-refresh 会对非组件导出告警，这里仅导出自定义 hook
// eslint-disable-next-line react-refresh/only-export-components
export const useThemeContext = () => {
  const context = useContext(ThemeContext)
  if (!context) throw new Error('useThemeContext must be used within ThemeProvider')
  return context
}

const THEME_STORAGE_KEY = 'antcode-theme-mode'

// Brand colors
const BRAND_COLORS = {
  primary: '#6366f1',
  success: '#10b981',
  warning: '#f59e0b',
  error: '#ef4444',
  info: '#3b82f6',
}

// Dark theme tokens
const DARK_TOKENS = {
  colorBgContainer: '#18181b',
  colorBgElevated: '#27272a',
  colorBgLayout: '#09090b',
  colorBgSpotlight: '#3f3f46',
  colorBorder: '#3f3f46',
  colorBorderSecondary: '#27272a',
  colorFill: '#3f3f46',
  colorFillSecondary: '#27272a',
  colorFillTertiary: '#18181b',
  colorFillQuaternary: '#09090b',
  colorText: 'rgba(255, 255, 255, 0.92)',
  colorTextSecondary: 'rgba(255, 255, 255, 0.68)',
  colorTextTertiary: 'rgba(255, 255, 255, 0.48)',
  colorTextQuaternary: 'rgba(255, 255, 255, 0.28)',
}

// Light theme tokens
const LIGHT_TOKENS = {
  colorBgContainer: '#ffffff',
  colorBgElevated: '#ffffff',
  colorBgLayout: '#f4f4f5',
  colorBgSpotlight: '#e4e4e7',
  colorBorder: '#e4e4e7',
  colorBorderSecondary: '#f4f4f5',
  colorFill: '#f4f4f5',
  colorFillSecondary: '#fafafa',
  colorFillTertiary: '#ffffff',
  colorFillQuaternary: '#ffffff',
  colorText: 'rgba(0, 0, 0, 0.88)',
  colorTextSecondary: 'rgba(0, 0, 0, 0.65)',
  colorTextTertiary: 'rgba(0, 0, 0, 0.45)',
  colorTextQuaternary: 'rgba(0, 0, 0, 0.25)',
}

export const ThemeProvider: React.FC<{ children: React.ReactNode }> = ({ children }) => {
  const [themeMode, setThemeModeState] = useState<ThemeMode>(() => {
    const saved = localStorage.getItem(THEME_STORAGE_KEY) as ThemeMode
    return saved || 'system'
  })

  const [systemDark, setSystemDark] = useState(() => 
    window.matchMedia('(prefers-color-scheme: dark)').matches
  )

  // Listen for system theme changes
  useEffect(() => {
    const mediaQuery = window.matchMedia('(prefers-color-scheme: dark)')
    const handler = (e: MediaQueryListEvent) => setSystemDark(e.matches)
    mediaQuery.addEventListener('change', handler)
    return () => mediaQuery.removeEventListener('change', handler)
  }, [])

  const isDark = useMemo(() => {
    if (themeMode === 'system') return systemDark
    return themeMode === 'dark'
  }, [themeMode, systemDark])

  const setThemeMode = useCallback((mode: ThemeMode) => {
    setThemeModeState(mode)
    localStorage.setItem(THEME_STORAGE_KEY, mode)
  }, [])

  // Apply theme to body
  useEffect(() => {
    const { body, documentElement: html } = document
    
    if (isDark) {
      body.classList.add('dark-theme')
      body.classList.remove('light-theme')
      html.classList.add('dark')
      html.classList.remove('light')
      html.style.setProperty('--app-bg', DARK_TOKENS.colorBgLayout)
      html.style.setProperty('--app-text', DARK_TOKENS.colorText)
    } else {
      body.classList.add('light-theme')
      body.classList.remove('dark-theme')
      html.classList.add('light')
      html.classList.remove('dark')
      html.style.setProperty('--app-bg', LIGHT_TOKENS.colorBgLayout)
      html.style.setProperty('--app-text', LIGHT_TOKENS.colorText)
    }
    
    body.style.backgroundColor = isDark ? DARK_TOKENS.colorBgLayout : LIGHT_TOKENS.colorBgLayout
    body.style.color = isDark ? DARK_TOKENS.colorText : LIGHT_TOKENS.colorText
  }, [isDark])

  // Generate Ant Design theme config
  const antdTheme: ThemeConfig = useMemo(() => {
    const { defaultAlgorithm, darkAlgorithm } = theme
    const tokens = isDark ? DARK_TOKENS : LIGHT_TOKENS

    return {
      algorithm: isDark ? darkAlgorithm : defaultAlgorithm,
      cssVar: true,
      hashed: false,
      token: {
        colorPrimary: BRAND_COLORS.primary,
        colorSuccess: BRAND_COLORS.success,
        colorWarning: BRAND_COLORS.warning,
        colorError: BRAND_COLORS.error,
        colorInfo: BRAND_COLORS.info,
        borderRadius: 8,
        borderRadiusLG: 12,
        borderRadiusSM: 6,
        borderRadiusXS: 4,
        fontFamily: '"Inter", -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif',
        fontFamilyCode: '"JetBrains Mono", "Fira Code", "SF Mono", Consolas, Monaco, monospace',
        fontSize: 14,
        motionDurationFast: '0.1s',
        motionDurationMid: '0.2s',
        motionDurationSlow: '0.3s',
        motionEaseInOut: 'cubic-bezier(0.4, 0, 0.2, 1)',
        motionEaseOut: 'cubic-bezier(0, 0, 0.2, 1)',
        ...tokens,
      },
      components: {
        Layout: {
          bodyBg: tokens.colorBgLayout,
          headerBg: tokens.colorBgContainer,
          siderBg: isDark ? '#0c0a09' : '#18181b',
          headerPadding: '0 24px',
          headerHeight: 64,
        },
        Menu: {
          itemBg: 'transparent',
          subMenuItemBg: 'transparent',
          itemSelectedBg: BRAND_COLORS.primary,
          itemHoverBg: 'rgba(255, 255, 255, 0.08)',
          itemActiveBg: BRAND_COLORS.primary,
          itemColor: 'rgba(255, 255, 255, 0.85)',
          itemSelectedColor: '#ffffff',
          itemHoverColor: '#ffffff',
          darkItemBg: 'transparent',
          darkItemSelectedBg: BRAND_COLORS.primary,
          darkItemHoverBg: 'rgba(255, 255, 255, 0.08)',
        },
        Card: {
          headerBg: tokens.colorBgElevated,
          borderRadiusLG: 12,
          paddingLG: 20,
        },
        Table: {
          headerBg: 'transparent',
          headerColor: tokens.colorText,
          colorText: tokens.colorText,
          colorTextHeading: tokens.colorText,
          rowHoverBg: isDark ? 'rgba(255, 255, 255, 0.02)' : 'rgba(0, 0, 0, 0.02)',
          borderColor: tokens.colorBorder,
          cellPaddingBlock: 12,
          cellPaddingInline: 16,
        },
        Button: {
          defaultBg: tokens.colorBgElevated,
          defaultBorderColor: tokens.colorBorder,
          defaultColor: tokens.colorText,
          borderRadius: 8,
          controlHeight: 36,
          controlHeightLG: 44,
          controlHeightSM: 28,
          paddingInline: 16,
          paddingInlineLG: 20,
        },
        Input: {
          colorBgContainer: tokens.colorBgContainer,
          colorBorder: tokens.colorBorder,
          borderRadius: 8,
          controlHeight: 36,
          paddingInline: 12,
        },
        Select: {
          colorBgContainer: tokens.colorBgContainer,
          colorBorder: tokens.colorBorder,
          borderRadius: 8,
          controlHeight: 36,
        },
        Modal: {
          colorBgElevated: tokens.colorBgElevated,
          colorBgMask: isDark ? 'rgba(0, 0, 0, 0.75)' : 'rgba(0, 0, 0, 0.55)',
          borderRadiusLG: 16,
          paddingContentHorizontalLG: 24,
        },
        Drawer: {
          colorBgElevated: tokens.colorBgElevated,
          colorBgMask: isDark ? 'rgba(0, 0, 0, 0.75)' : 'rgba(0, 0, 0, 0.55)',
        },
        Dropdown: {
          colorBgElevated: tokens.colorBgElevated,
          borderRadiusLG: 12,
          paddingBlock: 8,
        },
        Tooltip: {
          colorBgSpotlight: isDark ? '#3f3f46' : '#27272a',
          borderRadius: 8,
        },
        Message: {
          contentBg: tokens.colorBgElevated,
          borderRadiusLG: 12,
        },
        Notification: {
          colorBgElevated: tokens.colorBgElevated,
          borderRadiusLG: 12,
        },
        Tabs: { cardBg: tokens.colorBgContainer, cardGutter: 4 },
        Tag: { borderRadiusSM: 6 },
        Badge: { colorBgContainer: tokens.colorBgContainer },
        Statistic: { contentFontSize: 28, titleFontSize: 14 },
        FloatButton: { colorBgElevated: tokens.colorBgElevated, borderRadiusLG: 16 },
        Progress: { circleTextFontSize: '1em' },
        Alert: { borderRadiusLG: 12 },
        Skeleton: {
          gradientFromColor: isDark ? 'rgba(255, 255, 255, 0.06)' : 'rgba(0, 0, 0, 0.06)',
          gradientToColor: isDark ? 'rgba(255, 255, 255, 0.15)' : 'rgba(0, 0, 0, 0.15)',
        },
      },
    }
  }, [isDark])

  const contextValue: ThemeContextType = useMemo(() => ({
    themeMode,
    isDark,
    setThemeMode,
    antdTheme,
    token: null,
  }), [themeMode, isDark, setThemeMode, antdTheme])

  return (
    <ThemeContext.Provider value={contextValue}>
      {children}
    </ThemeContext.Provider>
  )
}
