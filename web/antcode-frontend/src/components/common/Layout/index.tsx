import React, { useState, useMemo, useCallback, memo } from 'react'
import { Layout as AntLayout, Menu, Avatar, Dropdown, Button, Space, Badge } from 'antd'
import {
  MenuFoldOutlined,
  MenuUnfoldOutlined,
  DashboardOutlined,
  ProjectOutlined,
  PlayCircleOutlined,
  FileTextOutlined,
  UserOutlined,
  LogoutOutlined,
  SettingOutlined,
  BellOutlined,
  TeamOutlined,
  MonitorOutlined
} from '@ant-design/icons'
import { useNavigate, useLocation, Outlet } from 'react-router-dom'
import { useAuth } from '@/hooks/useAuth'
import { APP_TITLE } from '@/utils/constants'
import ThemeToggle from '@/components/common/ThemeToggle'
import type { MenuItem } from '@/types'
import styles from './Layout.module.css'

const { Header, Sider, Content } = AntLayout

const Layout: React.FC = () => {
  const navigate = useNavigate()
  const location = useLocation()
  const { user, logout, hasPermission } = useAuth()
  const [collapsed, setCollapsed] = useState(false)

  // 菜单项配置
  const menuItems: MenuItem[] = [
    {
      key: '/dashboard',
      label: '仪表板',
      icon: <DashboardOutlined />,
      path: '/dashboard'
    },
    {
      key: '/envs',
      label: '环境管理',
      icon: <SettingOutlined />,
      path: '/envs'
    },
    {
      key: '/projects',
      label: '项目管理',
      icon: <ProjectOutlined />,
      path: '/projects'
    },
    {
      key: '/tasks',
      label: '任务管理',
      icon: <PlayCircleOutlined />,
      path: '/tasks'
    },
    // 管理员专用菜单
    {
      key: '/user-management',
      label: '用户管理',
      icon: <TeamOutlined />,
      path: '/user-management',
      hidden: !user?.is_admin // 只有管理员才显示
    }
  ]

  // 过滤用户有权限的菜单项
  const filteredMenuItems = menuItems.filter((item) => {
    // 过滤隐藏的菜单项
    if (item.hidden) {
      return false
    }
    // 这里可以根据权限过滤菜单项
    // 例如：return hasPermission(item.permission)
    return true
  })

  // 用户下拉菜单
  const userMenuItems = [
    {
      key: 'settings',
      label: '用户设置',
      icon: <SettingOutlined />,
      onClick: () => navigate('/settings')
    },
    {
      type: 'divider' as const
    },
    {
      key: 'logout',
      label: '退出登录',
      icon: <LogoutOutlined />,
      onClick: logout
    }
  ]

  // 处理菜单点击
  const handleMenuClick = ({ key }: { key: string }) => {
    const menuItem = filteredMenuItems.find(item => item.key === key)
    if (menuItem?.path) {
      navigate(menuItem.path)
    }
  }

  // 获取当前选中的菜单项
  const selectedKeys = [location.pathname]

  return (
    <AntLayout className={styles.layout}>
      {/* 侧边栏 */}
      <Sider
        trigger={null}
        collapsible
        collapsed={collapsed}
        className={styles.sider}
        width={256}
        collapsedWidth={80}
      >
        {/* Logo */}
        <div className={styles.logo}>
          {collapsed ? (
            <div className={styles.logoCollapsed}>A</div>
          ) : (
            <div className={styles.logoFull}>
              <span className={styles.logoText}>{APP_TITLE}</span>
            </div>
          )}
        </div>

        {/* 菜单 */}
        <Menu
          theme="dark"
          mode="inline"
          selectedKeys={selectedKeys}
          onClick={handleMenuClick}
          className={styles.menu}
          items={filteredMenuItems.map(item => ({
            key: item.key,
            icon: item.icon,
            label: item.label
          }))}
        />
      </Sider>

      {/* 主内容区 */}
      <AntLayout className={`${styles.mainLayout} ${collapsed ? styles.collapsed : ''}`}>
        {/* 头部 */}
        <Header className={styles.header}>
          <div className={styles.headerLeft}>
            <Button
              type="text"
              icon={collapsed ? <MenuUnfoldOutlined /> : <MenuFoldOutlined />}
              onClick={() => setCollapsed(!collapsed)}
              className={styles.trigger}
            />
          </div>

          <div className={styles.headerRight}>
            <Space size="middle">
              {/* 主题切换 */}
              <ThemeToggle />

              {/* 通知 */}
              <Badge count={0} size="small">
                <Button
                  type="text"
                  icon={<BellOutlined />}
                  className={styles.headerButton}
                />
              </Badge>

              {/* 用户信息 */}
              <Dropdown
                menu={{ items: userMenuItems }}
                placement="bottomRight"
                arrow
              >
                <div className={styles.userInfo}>
                  <Avatar
                    size="small"
                    icon={<UserOutlined />}
                    className={styles.avatar}
                  />
                  <span className={styles.username}>{user?.username}</span>
                </div>
              </Dropdown>
            </Space>
          </div>
        </Header>

        {/* 内容区 */}
        <Content className={styles.content}>
          <div className={styles.contentInner}>
            <Outlet />
          </div>
        </Content>
      </AntLayout>
    </AntLayout>
  )
}

export default memo(Layout)
