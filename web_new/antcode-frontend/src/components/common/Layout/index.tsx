import React, { useState, useEffect, memo, useCallback } from 'react'
import { Layout as AntLayout, Menu, Avatar, Dropdown, Button, Badge, Flex, Typography, theme } from 'antd'
import {
  MenuFoldOutlined,
  MenuUnfoldOutlined,
  DashboardOutlined,
  ProjectOutlined,
  PlayCircleOutlined,
  UserOutlined,
  LogoutOutlined,
  SettingOutlined,
  BellOutlined,
  TeamOutlined,
  ClockCircleOutlined,
  CopyrightOutlined,
  GithubOutlined,
  ClusterOutlined,
  FileTextOutlined,
  ToolOutlined
} from '@ant-design/icons'
import { useNavigate, useLocation, Outlet } from 'react-router-dom'
import { useAuth } from '@/hooks/useAuth'
import { useSystemStore } from '@/stores/systemStore'
import { APP_LOGO_ICON, APP_LOGO_SHORT } from '@/config/app'
import ThemeToggle from '@/components/common/ThemeToggle'
import DynamicIcon from '@/components/common/DynamicIcon'
import WorkerSelector from '@/components/common/WorkerSelector'
import type { MenuItem } from '@/types'
import styles from './Layout.module.css'

const { Header, Sider, Content, Footer } = AntLayout
const { Text } = Typography

const Layout: React.FC = () => {
  const navigate = useNavigate()
  const location = useLocation()
  const { user, logout } = useAuth()
  const { token } = theme.useToken()
  const { appInfo, fetchAppInfo } = useSystemStore()
  const [collapsed, setCollapsed] = useState(false)
  const [currentTime, setCurrentTime] = useState(new Date())

  useEffect(() => {
    fetchAppInfo()
  }, [fetchAppInfo])

  // 从后端获取的应用信息，带默认值
  const appName = appInfo?.name || 'AntCode'  // 侧边栏用
  const appTitle = appInfo?.title || 'AntCode 任务调度平台'  // 页脚用
  const appVersion = appInfo?.version || ''
  const copyrightYear = appInfo?.copyright_year || '2025'

  const menuItems: MenuItem[] = [
    { key: '/dashboard', label: '仪表板', icon: <DashboardOutlined />, path: '/dashboard' },
    { key: '/workers', label: 'Worker 管理', icon: <ClusterOutlined />, path: '/workers', hidden: !user?.is_admin },
    { key: '/runtimes', label: '运行时管理', icon: <ToolOutlined />, path: '/runtimes' },
    { key: '/projects', label: '项目管理', icon: <ProjectOutlined />, path: '/projects' },
    { key: '/tasks', label: '任务管理', icon: <PlayCircleOutlined />, path: '/tasks' },
    { key: '/user-management', label: '用户管理', icon: <TeamOutlined />, path: '/user-management', hidden: !user?.is_admin },
    { key: '/alert-config', label: '告警配置', icon: <BellOutlined />, path: '/alert-config', hidden: !user?.is_admin },
    { key: '/audit-log', label: '审计日志', icon: <FileTextOutlined />, path: '/audit-log', hidden: !user?.is_admin },
    { key: '/system-config', label: '系统配置', icon: <SettingOutlined />, path: '/system-config', hidden: user?.username !== 'admin' },
  ]

  const filteredMenuItems = menuItems.filter((item) => !item.hidden)

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
      <Sider trigger={null} collapsible collapsed={collapsed} className={styles.sider} width={260} collapsedWidth={80}>
        <Flex align="center" justify={collapsed ? 'center' : 'flex-start'} className={styles.logo}>
          {collapsed ? (
            <div className={styles.logoCollapsed}>{APP_LOGO_SHORT}</div>
          ) : (
            <Flex align="center" gap={8} className={styles.logoFull}>
              <div className={styles.logoIcon}>
                <DynamicIcon name={APP_LOGO_ICON} style={{ fontSize: 24 }} />
              </div>
              <span className={styles.logoText}>{appName}</span>
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
              {user?.is_admin && <WorkerSelector />}
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
            <Outlet />
          </div>
        </Content>

        <Footer className={styles.footer} style={{ background: 'transparent', borderTop: `1px solid ${token.colorBorderSecondary}` }}>
          <Flex align="center" justify="space-between" wrap="wrap" gap={8}>
            <Flex align="center" gap={8}>
              <CopyrightOutlined style={{ color: token.colorTextSecondary }} />
              <Text type="secondary" style={{ fontSize: 12 }}>{copyrightYear} {appTitle}</Text>
              {appVersion && (
                <>
                  <span style={{ color: token.colorBorderSecondary }}>|</span>
                  <Text type="secondary" style={{ fontSize: 12 }}>v{appVersion}</Text>
                </>
              )}
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
