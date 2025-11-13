import React, { createContext, useContext } from 'react'
import { ConfigProvider } from 'antd'
import zhCN from 'antd/locale/zh_CN'
import { useTheme, type ThemeMode } from '@/hooks/useTheme'

interface ThemeContextType {
  themeMode: ThemeMode
  isDark: boolean
  setThemeMode: (mode: ThemeMode) => void
}

const ThemeContext = createContext<ThemeContextType | undefined>(undefined)

export const useThemeContext = () => {
  const context = useContext(ThemeContext)
  if (!context) {
    throw new Error('useThemeContext must be used within a ThemeProvider')
  }
  return context
}

interface ThemeProviderProps {
  children: React.ReactNode
}

export const ThemeProvider: React.FC<ThemeProviderProps> = ({ children }) => {
  const { themeMode, isDark, setThemeMode, antdTheme } = useTheme()

  const contextValue: ThemeContextType = {
    themeMode,
    isDark,
    setThemeMode
  }

  return (
    <ThemeContext.Provider value={contextValue}>
      <ConfigProvider
        theme={antdTheme}
        locale={zhCN}
        getPopupContainer={() => {
          // 始终使用 document.body 作为容器，避免被表格等元素遮挡
          return document.body
        }}
      >
        {children}
      </ConfigProvider>
    </ThemeContext.Provider>
  )
}
