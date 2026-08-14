import type React from 'react'
import { useState, useEffect, memo, useCallback } from 'react'
import { Layout as AntLayout, Menu, Avatar, Dropdown, Button, Badge, Flex, Typography, theme } from 'antd'
import {
  MenuFoldOutlined,
  MenuUnfoldOutlined,
  UserOutlined,
  LogoutOutlined,
  SettingOutlined,
  BellOutlined,
  ClockCircleOutlined,
  CopyrightOutlined,
  GithubOutlined,
} from '@ant-design/icons'
import { useNavigate, useLocation, Outlet } from 'react-router'
import ErrorBoundary from '@/components/common/ErrorBoundary'
import { useAuth } from '@/hooks/useAuth'
import ThemeToggle from '@/components/common/ThemeToggle'
import DynamicIcon from '@/components/common/DynamicIcon'
import { useBrandingStore } from '@/stores/brandingStore'
import WorkerSelector from '@/components/common/WorkerSelector'
import { createMenuItems } from './menuItems'
import styles from './Layout.module.css'

const { Header, Sider, Content, Footer } = AntLayout
const { Text } = Typography

const WORKER_RELEVANT_PATHS = ['/dashboard', '/tasks', '/projects', '/workers', '/repositories']

const Layout: React.FC = () => {
  const navigate = useNavigate()
  const location = useLocation()
  const { user, logout } = useAuth()
  const branding = useBrandingStore((state) => state.branding)
  const { token } = theme.useToken()
  const [collapsed, setCollapsed] = useState(false)
  const [currentTime, setCurrentTime] = useState(new Date())

  const filteredMenuItems = createMenuItems(user).filter((item) => !item.hidden)

  const userMenuItems = [
    { key: 'settings', label: '用户设置', icon: <SettingOutlined />, onClick: () => navigate('/settings') },
    { type: 'divider' as const },
    { key: 'logout', label: '退出登录', icon: <LogoutOutlined />, danger: true, onClick: logout },
  ]

  const handleMenuClick = useCallback(({ key }: { key: string }) => {
    const menuItem = filteredMenuItems.find(item => item.key === key)
    if (menuItem?.path) navigate(menuItem.path)
  }, [filteredMenuItems, navigate])

  const selectedKeys = [location.pathname]
  const showWorkerSelector = WORKER_RELEVANT_PATHS.some(p => location.pathname.startsWith(p))

  useEffect(() => {
    const timer = setInterval(() => setCurrentTime(new Date()), 1000)
    return () => clearInterval(timer)
  }, [])

  const formatTime = (date: Date) => {
    const pad = (n: number) => String(n).padStart(2, '0')
    return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())} ${pad(date.getHours())}:${pad(date.getMinutes())}:${pad(date.getSeconds())}`
  }

  return (
    <AntLayout className={styles.layout}>
      <Sider trigger={null} collapsible collapsed={collapsed} className={styles.sider} width={200} collapsedWidth={64}>
        <Flex align="center" justify="center" className={styles.logo}>
          {collapsed ? (
            <div className={`${styles.logoCollapsed} ${branding.logoUrl ? styles.logoCollapsedImage : ''}`}>
              {branding.logoUrl ? (
                <img src={branding.logoUrl} alt={branding.brandName} className={styles.logoImageCollapsed} />
              ) : (
                <DynamicIcon name={branding.logoIcon} className={styles.logoCollapsedIcon} />
              )}
            </div>
          ) : (
            <Flex align="center" gap={8} className={styles.logoFull}>
              <div className={`${styles.logoIcon} ${branding.logoUrl ? styles.logoIconImage : ''}`}>
                {branding.logoUrl ? (
                  <img src={branding.logoUrl} alt={branding.brandName} className={styles.logoImage} />
                ) : (
                  <DynamicIcon name={branding.logoIcon} style={{ fontSize: 24 }} />
                )}
              </div>
              <span className={styles.logoText}>{branding.logoText}</span>
            </Flex>
          )}
        </Flex>
        <Menu
          theme="dark"
          mode="inline"
          selectedKeys={selectedKeys}
          onClick={handleMenuClick}
          className={styles.menu}
          items={filteredMenuItems.map(item => ({ key: item.key, icon: item.icon, label: item.label }))}
        />
      </Sider>

      <AntLayout className={`${styles.mainLayout} ${collapsed ? styles.collapsed : ''}`} style={{ background: token.colorBgLayout }}>
        <Header className={styles.header} style={{ background: token.colorBgContainer, borderBottom: `1px solid ${token.colorBorderSecondary}` }}>
          <Flex align="center" justify="space-between" style={{ height: '100%' }}>
            <Flex align="center" gap={16}>
              <Button type="text" icon={collapsed ? <MenuUnfoldOutlined /> : <MenuFoldOutlined />} onClick={() => setCollapsed(!collapsed)} className={styles.trigger} />
              {showWorkerSelector && <WorkerSelector className={styles.workerSelector} />}
            </Flex>
            <Flex align="center" gap={12}>
              <ThemeToggle />
              <Badge count={0} size="small">
                <Button type="text" icon={<BellOutlined />} className={styles.headerButton} />
              </Badge>
              <Dropdown menu={{ items: userMenuItems }} placement="bottomRight" arrow={{ pointAtCenter: true }}>
                <Flex align="center" gap={8} className={styles.userInfo}>
                  <Avatar size={32} icon={<UserOutlined />} style={{ backgroundColor: token.colorPrimary, cursor: 'pointer' }} />
                  <Text className={styles.username} ellipsis={{ tooltip: user?.username }}>{user?.username}</Text>
                </Flex>
              </Dropdown>
            </Flex>
          </Flex>
        </Header>

        <Content className={styles.content}>
          <div className={styles.contentInner}>
            {/* key=pathname 让每次路由切换 ErrorBoundary 重置，
                单个页面崩溃时导航/其它页面不受影响 */}
            <ErrorBoundary key={location.pathname}>
              <Outlet />
            </ErrorBoundary>
          </div>
        </Content>

        <Footer className={styles.footer} style={{ background: 'transparent', borderTop: `1px solid ${token.colorBorderSecondary}` }}>
          <Flex align="center" justify="space-between" wrap="wrap" gap={8}>
            <Flex align="center" gap={8}>
              <CopyrightOutlined style={{ color: token.colorTextSecondary }} />
              <Text type="secondary" style={{ fontSize: 12 }}>2025 {branding.appTitle}</Text>
              <span style={{ color: token.colorBorderSecondary }}>|</span>
              <a href="https://github.com/xyuns-cc/AntCode" target="_blank" rel="noopener noreferrer" className={styles.footerLink}>
                <GithubOutlined /><span>GitHub</span>
              </a>
            </Flex>
            <Flex align="center" gap={6}>
              <ClockCircleOutlined style={{ color: token.colorTextSecondary }} />
              <Text type="secondary" style={{ fontSize: 12, fontFamily: 'var(--ant-font-family-code)', letterSpacing: '0.5px' }}>
                {formatTime(currentTime)}
              </Text>
            </Flex>
          </Flex>
        </Footer>
      </AntLayout>
    </AntLayout>
  )
}

export default memo(Layout)
