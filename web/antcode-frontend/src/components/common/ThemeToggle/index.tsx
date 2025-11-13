import React from 'react'
import { Dropdown, Button, Space, Typography } from 'antd'
import {
  SunOutlined,
  MoonOutlined,
  DesktopOutlined,
  CheckOutlined
} from '@ant-design/icons'
import { useThemeContext } from '@/contexts/ThemeContext'
import type { ThemeMode } from '@/hooks/useTheme'
import type { MenuProps } from 'antd'

const { Text } = Typography

const ThemeToggle: React.FC = () => {
  const { themeMode, isDark, setThemeMode } = useThemeContext()

  const themeOptions = [
    {
      key: 'light',
      label: '浅色主题',
      icon: <SunOutlined />,
      description: '始终使用浅色主题'
    },
    {
      key: 'dark',
      label: '深色主题',
      icon: <MoonOutlined />,
      description: '始终使用深色主题'
    },
    {
      key: 'system',
      label: '跟随系统',
      icon: <DesktopOutlined />,
      description: '根据系统设置自动切换'
    }
  ]

  const handleThemeChange = (mode: ThemeMode) => {
    setThemeMode(mode)
  }

  const items: MenuProps['items'] = themeOptions.map((option) => ({
    key: option.key,
    label: (
      <div
        style={{
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'space-between',
          minWidth: 180,
          padding: '4px 0'
        }}
        onClick={() => handleThemeChange(option.key as ThemeMode)}
      >
        <Space>
          <span style={{ fontSize: 16 }}>{option.icon}</span>
          <div>
            <div style={{ fontWeight: themeMode === option.key ? 'bold' : 'normal' }}>
              {option.label}
            </div>
            <Text type="secondary" style={{ fontSize: 12 }}>
              {option.description}
            </Text>
          </div>
        </Space>
        {themeMode === option.key && (
          <CheckOutlined style={{ color: '#1890ff', fontSize: 14 }} />
        )}
      </div>
    )
  }))

  // 获取当前主题的图标
  const getCurrentIcon = () => {
    switch (themeMode) {
      case 'light':
        return <SunOutlined />
      case 'dark':
        return <MoonOutlined />
      case 'system':
        return <DesktopOutlined />
      default:
        return <SunOutlined />
    }
  }

  // 获取当前主题的标签
  const getCurrentLabel = () => {
    switch (themeMode) {
      case 'light':
        return '浅色'
      case 'dark':
        return '深色'
      case 'system':
        return '系统'
      default:
        return '浅色'
    }
  }

  return (
    <Dropdown
      menu={{ items }}
      placement="bottomRight"
      trigger={['click']}
      overlayStyle={{
        minWidth: 200
      }}
    >
      <Button
        type="text"
        size="small"
        style={{
          display: 'flex',
          alignItems: 'center',
          gap: 4,
          height: 32,
          padding: '0 8px',
          borderRadius: 6
        }}
      >
        <span style={{ fontSize: 16 }}>{getCurrentIcon()}</span>
        <span style={{ fontSize: 12 }}>{getCurrentLabel()}</span>
      </Button>
    </Dropdown>
  )
}

export default ThemeToggle
