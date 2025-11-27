import { useThemeContext, type ThemeMode } from '@/contexts/ThemeContext'

export type { ThemeMode }

/** @deprecated Use useThemeContext directly */
export const useTheme = () => {
  const { themeMode, isDark, setThemeMode, antdTheme } = useThemeContext()
  return { themeMode, isDark, setThemeMode, antdTheme }
}
