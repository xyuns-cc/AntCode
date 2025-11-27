import React from 'react'
import { Dropdown, Button, Flex, Typography, theme } from 'antd'
import { SunOutlined, MoonOutlined, DesktopOutlined, CheckOutlined } from '@ant-design/icons'
import { useThemeContext } from '@/contexts/ThemeContext'
import type { ThemeMode } from '@/hooks/useTheme'
import type { MenuProps } from 'antd'

const { Text } = Typography

const themeOptions = [
  { key: 'light', label: '浅色主题', icon: <SunOutlined />, description: '始终使用浅色主题' },
  { key: 'dark', label: '深色主题', icon: <MoonOutlined />, description: '始终使用深色主题' },
  { key: 'system', label: '跟随系统', icon: <DesktopOutlined />, description: '根据系统设置自动切换' },
]

const ThemeToggle: React.FC = () => {
  const { themeMode, setThemeMode } = useThemeContext()
  const { token } = theme.useToken()

  const items: MenuProps['items'] = themeOptions.map((option) => ({
    key: option.key,
    label: (
      <Flex align="center" justify="space-between" style={{ minWidth: 200, padding: '6px 0' }} onClick={() => setThemeMode(option.key as ThemeMode)}>
        <Flex align="center" gap={12}>
          <Flex align="center" justify="center" style={{
            width: 32, height: 32, borderRadius: 8,
            background: themeMode === option.key ? `${token.colorPrimary}15` : token.colorFillTertiary,
            color: themeMode === option.key ? token.colorPrimary : token.colorTextSecondary,
            fontSize: 16,
          }}>
            {option.icon}
          </Flex>
          <div>
            <div style={{ fontWeight: themeMode === option.key ? 600 : 400, color: token.colorText }}>{option.label}</div>
            <Text type="secondary" style={{ fontSize: 12 }}>{option.description}</Text>
          </div>
        </Flex>
        {themeMode === option.key && <CheckOutlined style={{ color: token.colorPrimary, fontSize: 14 }} />}
      </Flex>
    ),
  }))

  const getCurrentIcon = () => {
    switch (themeMode) {
      case 'light': return <SunOutlined />
      case 'dark': return <MoonOutlined />
      default: return <DesktopOutlined />
    }
  }

  return (
    <Dropdown menu={{ items }} placement="bottomRight" trigger={['click']} arrow={{ pointAtCenter: true }}>
      <Button type="text" style={{ display: 'flex', alignItems: 'center', justifyContent: 'center', width: 40, height: 40, borderRadius: 10, fontSize: 18 }}>
        {getCurrentIcon()}
      </Button>
    </Dropdown>
  )
}

export default ThemeToggle
