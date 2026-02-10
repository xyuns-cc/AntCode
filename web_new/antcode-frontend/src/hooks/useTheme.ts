import { useThemeContext, type ThemeMode } from '@/contexts/ThemeContext'

export type { ThemeMode }

export const useTheme = () => {
  const { themeMode, isDark, setThemeMode, antdTheme } = useThemeContext()
  return { themeMode, isDark, setThemeMode, antdTheme }
}
