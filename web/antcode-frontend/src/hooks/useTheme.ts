import { useState, useEffect, useCallback } from 'react'
import { theme } from 'antd'

export type ThemeMode = 'light' | 'dark' | 'system'

interface ThemeConfig {
  mode: ThemeMode
  isDark: boolean
}

const THEME_STORAGE_KEY = 'antcode-theme-mode'

export const useTheme = () => {
  const [themeConfig, setThemeConfig] = useState<ThemeConfig>(() => {
    // 从 localStorage 获取保存的主题设置
    const savedMode = localStorage.getItem(THEME_STORAGE_KEY) as ThemeMode
    const mode = savedMode || 'system'
    
    // 获取系统主题偏好
    const systemPrefersDark = window.matchMedia('(prefers-color-scheme: dark)').matches
    
    // 确定当前是否应该使用深色主题
    const isDark = mode === 'dark' || (mode === 'system' && systemPrefersDark)
    
    return { mode, isDark }
  })

  // 监听系统主题变化
  useEffect(() => {
    const mediaQuery = window.matchMedia('(prefers-color-scheme: dark)')
    
    const handleSystemThemeChange = (e: MediaQueryListEvent) => {
      if (themeConfig.mode === 'system') {
        setThemeConfig(prev => ({
          ...prev,
          isDark: e.matches
        }))
      }
    }

    mediaQuery.addEventListener('change', handleSystemThemeChange)
    
    return () => {
      mediaQuery.removeEventListener('change', handleSystemThemeChange)
    }
  }, [themeConfig.mode])

  // 切换主题模式
  const setThemeMode = useCallback((mode: ThemeMode) => {
    const systemPrefersDark = window.matchMedia('(prefers-color-scheme: dark)').matches
    const isDark = mode === 'dark' || (mode === 'system' && systemPrefersDark)
    
    setThemeConfig({ mode, isDark })
    localStorage.setItem(THEME_STORAGE_KEY, mode)
  }, [])

  // 获取 Ant Design 主题配置
  const getAntdTheme = useCallback(() => {
    const { defaultAlgorithm, darkAlgorithm } = theme
    
    return {
      algorithm: themeConfig.isDark ? darkAlgorithm : defaultAlgorithm,
      token: {
        colorPrimary: '#1890ff',
        borderRadius: 6,
        ...(themeConfig.isDark ? {
          // 深色主题的自定义 token
          colorBgContainer: '#141414',
          colorBgElevated: '#1f1f1f',
          colorBgLayout: '#000000',
          colorBorder: '#303030',
          colorBorderSecondary: '#262626',
          colorFill: '#262626',
          colorFillSecondary: '#1f1f1f',
          colorFillTertiary: '#141414',
          colorFillQuaternary: '#0c0c0c',
          colorText: 'rgba(255, 255, 255, 0.88)',
          colorTextSecondary: 'rgba(255, 255, 255, 0.65)',
          colorTextTertiary: 'rgba(255, 255, 255, 0.45)',
          colorTextQuaternary: 'rgba(255, 255, 255, 0.25)',
        } : {
          // 浅色主题的自定义 token
          colorBgContainer: '#ffffff',
          colorBgElevated: '#ffffff',
          colorBgLayout: '#f5f5f5',
          colorBorder: '#d9d9d9',
          colorBorderSecondary: '#f0f0f0',
          colorFill: '#f5f5f5',
          colorFillSecondary: '#fafafa',
          colorFillTertiary: '#ffffff',
          colorFillQuaternary: '#ffffff',
          colorText: 'rgba(0, 0, 0, 0.88)',
          colorTextSecondary: 'rgba(0, 0, 0, 0.65)',
          colorTextTertiary: 'rgba(0, 0, 0, 0.45)',
          colorTextQuaternary: 'rgba(0, 0, 0, 0.25)',
        })
      },
      components: {
        Layout: {
          bodyBg: themeConfig.isDark ? '#000000' : '#f5f5f5',
          headerBg: themeConfig.isDark ? '#141414' : '#ffffff',
          siderBg: '#001529', // 侧边栏始终保持深蓝色
          triggerBg: themeConfig.isDark ? '#262626' : '#f0f0f0',
          triggerColor: themeConfig.isDark ? 'rgba(255, 255, 255, 0.85)' : 'rgba(0, 0, 0, 0.85)',
        },
        Menu: {
          itemBg: 'transparent',
          subMenuItemBg: 'transparent',
          itemSelectedBg: themeConfig.isDark ? '#1890ff' : '#1890ff',
          itemHoverBg: themeConfig.isDark ? 'rgba(255, 255, 255, 0.1)' : 'rgba(255, 255, 255, 0.1)',
          itemActiveBg: themeConfig.isDark ? '#1890ff' : '#1890ff',
          itemColor: themeConfig.isDark ? 'rgba(255, 255, 255, 0.88)' : 'rgba(255, 255, 255, 0.88)',
          itemSelectedColor: '#ffffff',
          itemHoverColor: '#ffffff',
        },
        Card: {
          headerBg: themeConfig.isDark ? '#1f1f1f' : '#fafafa',
        },
        Table: {
          headerBg: themeConfig.isDark ? 'transparent' : 'transparent',
          headerColor: themeConfig.isDark ? 'rgba(255, 255, 255, 0.85)' : 'rgba(0, 0, 0, 0.85)',
          colorText: themeConfig.isDark ? 'rgba(255, 255, 255, 0.88)' : 'rgba(0, 0, 0, 0.88)',
          colorTextHeading: themeConfig.isDark ? 'rgba(255, 255, 255, 0.85)' : 'rgba(0, 0, 0, 0.85)',
          rowHoverBg: themeConfig.isDark ? 'rgba(255, 255, 255, 0.02)' : 'rgba(0, 0, 0, 0.02)',
          borderColor: themeConfig.isDark ? 'rgba(255, 255, 255, 0.06)' : 'rgba(0, 0, 0, 0.06)',
        },
        Button: {
          defaultBg: themeConfig.isDark ? '#262626' : '#ffffff',
          defaultBorderColor: themeConfig.isDark ? '#434343' : '#d9d9d9',
          defaultColor: themeConfig.isDark ? 'rgba(255, 255, 255, 0.88)' : 'rgba(0, 0, 0, 0.88)',
          dangerColor: '#ff4d4f',
          colorErrorBg: themeConfig.isDark ? '#2a1215' : '#fff2f0',
          colorErrorBorder: themeConfig.isDark ? '#58181c' : '#ffccc7',
          colorErrorHover: '#ff7875',
        },
        Input: {
          colorBgContainer: themeConfig.isDark ? '#141414' : '#ffffff',
          colorBorder: themeConfig.isDark ? '#434343' : '#d9d9d9',
        },
        Select: {
          colorBgContainer: themeConfig.isDark ? '#141414' : '#ffffff',
          colorBorder: themeConfig.isDark ? '#434343' : '#d9d9d9',
        },
        Drawer: {
          colorBgElevated: themeConfig.isDark ? '#1f1f1f' : '#ffffff',
          colorBgMask: themeConfig.isDark ? 'rgba(0, 0, 0, 0.65)' : 'rgba(0, 0, 0, 0.45)',
        },
        Modal: {
          colorBgElevated: themeConfig.isDark ? '#1f1f1f' : '#ffffff',
          colorTextHeading: themeConfig.isDark ? 'rgba(255, 255, 255, 0.88)' : 'rgba(0, 0, 0, 0.88)',
          colorText: themeConfig.isDark ? 'rgba(255, 255, 255, 0.88)' : 'rgba(0, 0, 0, 0.88)',
          colorBgMask: themeConfig.isDark ? 'rgba(0, 0, 0, 0.65)' : 'rgba(0, 0, 0, 0.45)',
          colorIcon: themeConfig.isDark ? 'rgba(255, 255, 255, 0.45)' : 'rgba(0, 0, 0, 0.45)',
          colorIconHover: themeConfig.isDark ? 'rgba(255, 255, 255, 0.88)' : 'rgba(0, 0, 0, 0.88)',
        }
      }
    }
  }, [themeConfig.isDark])

  // 应用主题到 body 元素
  useEffect(() => {
    const body = document.body
    
    if (themeConfig.isDark) {
      body.classList.add('dark-theme')
      body.classList.remove('light-theme')
      body.style.backgroundColor = '#000000'
      body.style.color = 'rgba(255, 255, 255, 0.88)'
    } else {
      body.classList.add('light-theme')
      body.classList.remove('dark-theme')
      body.style.backgroundColor = '#f5f5f5'
      body.style.color = 'rgba(0, 0, 0, 0.88)'
    }
  }, [themeConfig.isDark])

  return {
    themeMode: themeConfig.mode,
    isDark: themeConfig.isDark,
    setThemeMode,
    antdTheme: getAntdTheme()
  }
}
