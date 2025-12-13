import React, { useEffect } from 'react'
import { BrowserRouter as Router, Routes, Route, Navigate, useLocation } from 'react-router-dom'
import { App as AntApp, ConfigProvider, FloatButton } from 'antd'
import { VerticalAlignTopOutlined } from '@ant-design/icons'
import zhCN from 'antd/locale/zh_CN'
import { ThemeProvider, useThemeContext } from '@/contexts/ThemeContext'
import Layout from '@/components/common/Layout'
import AuthGuard from '@/components/common/AuthGuard'
import AdminRoute from '@/components/common/AdminRoute'
import SuperAdminRoute from '@/components/common/SuperAdminRoute'
import AppInitializer from '@/components/common/AppInitializer'
import { lazyLoad } from '@/utils/lazyLoad'
import { useAuth } from '@/hooks/useAuth'
import { STORAGE_KEYS } from '@/utils/constants'
import { AuthHandler } from '@/utils/authHandler'
import '@/styles/globals.css'
import '@/styles/variables.css'
import '@/styles/antd-fixes.css'

// Lazy-loaded pages
const Login = lazyLoad(() => import('@/pages/Login'))
const Dashboard = lazyLoad(() => import('@/pages/Dashboard'))
const Nodes = lazyLoad(() => import('@/pages/Nodes'))
const Projects = lazyLoad(() => import('@/pages/Projects'))
const Tasks = lazyLoad(() => import('@/pages/Tasks'))
const Settings = lazyLoad(() => import('@/pages/Settings'))
const Envs = lazyLoad(() => import('@/pages/Envs'))
const UserManagement = lazyLoad(() => import('@/pages/UserManagement'))
const SystemConfig = lazyLoad(() => import('@/pages/SystemConfig'))
const TaskCreate = lazyLoad(() => import('@/pages/Tasks/TaskCreate'))
const TaskDetail = lazyLoad(() => import('@/pages/Tasks/TaskDetail'))
const TaskEdit = lazyLoad(() => import('@/pages/Tasks/TaskEdit'))
const ExecutionLogs = lazyLoad(() => import('@/pages/Tasks/ExecutionLogs'))
const Monitor = lazyLoad(() => import('@/pages/Monitor'))
const AlertConfig = lazyLoad(() => import('@/pages/AlertConfig'))
const AuditLog = lazyLoad(() => import('@/pages/AuditLog'))

// Route auth checker - validates token on route change
const RouteAuthChecker: React.FC = () => {
  const location = useLocation()
  const { isAuthenticated, clearUser } = useAuth()

  useEffect(() => {
    const checkAuth = () => {
      const token = localStorage.getItem(STORAGE_KEYS.ACCESS_TOKEN)
      if (location.pathname !== '/login' && !token && isAuthenticated) {
        clearUser()
        AuthHandler.handleAuthFailure(true)
      }
    }
    const timeoutId = setTimeout(checkAuth, 100)
    return () => clearTimeout(timeoutId)
  }, [location.pathname, isAuthenticated, clearUser])

  return null
}

// Protected route wrapper
const ProtectedRoute: React.FC<{ children: React.ReactNode }> = ({ children }) => {
  const { isAuthenticated, clearUser } = useAuth()
  const hasToken = localStorage.getItem(STORAGE_KEYS.ACCESS_TOKEN)

  useEffect(() => {
    // 如果已认证但没有token，清除用户状态
    if (isAuthenticated && !hasToken) {
      clearUser()
    }
  }, [isAuthenticated, hasToken, clearUser])

  if (!isAuthenticated || !hasToken) {
    return <Navigate to="/login" replace />
  }

  return <>{children}</>
}

// Public route wrapper - redirects authenticated users
const PublicRoute: React.FC<{ children: React.ReactNode }> = ({ children }) => {
  const { isAuthenticated } = useAuth()
  if (isAuthenticated) return <Navigate to="/dashboard" replace />
  return <>{children}</>
}

// Float button - back to top
const FloatButtonGroup: React.FC = () => (
  <FloatButton.BackTop 
    icon={<VerticalAlignTopOutlined />}
    tooltip="回到顶部"
    visibilityHeight={200}
    style={{ insetInlineEnd: 24 }}
  />
)

// App routes
const AppRoutes: React.FC = () => (
  <>
    <RouteAuthChecker />
    <Routes>
      <Route path="/login" element={<PublicRoute><Login /></PublicRoute>} />
      <Route path="/" element={<ProtectedRoute><Layout /></ProtectedRoute>}>
        <Route index element={<Navigate to="/dashboard" replace />} />
        <Route path="dashboard" element={<Dashboard />} />
        <Route path="nodes" element={<Nodes />} />
        <Route path="monitor" element={<Monitor />} />
        <Route path="projects/*" element={<Projects />} />
        <Route path="envs" element={<Envs />} />
        <Route path="tasks" element={<Tasks />} />
        <Route path="tasks/create" element={<TaskCreate />} />
        <Route path="tasks/:id/edit" element={<TaskEdit />} />
        <Route path="tasks/:id" element={<TaskDetail />} />
        <Route path="tasks/:taskId/executions/:executionId" element={<ExecutionLogs />} />
        <Route path="user-management" element={<AdminRoute><UserManagement /></AdminRoute>} />
        <Route path="system-config" element={<SuperAdminRoute><SystemConfig /></SuperAdminRoute>} />
        <Route path="alert-config" element={<AdminRoute><AlertConfig /></AdminRoute>} />
        <Route path="audit-log" element={<AdminRoute><AuditLog /></AdminRoute>} />
        <Route path="settings" element={<Settings />} />
      </Route>
      <Route path="*" element={<Navigate to="/dashboard" replace />} />
    </Routes>
    <FloatButtonGroup />
  </>
)

// Inner app with theme context
const AppContent: React.FC = () => {
  const { antdTheme } = useThemeContext()

  return (
    <ConfigProvider
      theme={antdTheme}
      locale={zhCN}
      getPopupContainer={() => document.body}
      wave={{ disabled: false }}
      warning={{ strict: false }}
    >
      <AntApp
        message={{ maxCount: 3, duration: 3, top: 70 }}
        notification={{ placement: 'topRight', maxCount: 5, top: 70 }}
      >
        <AppInitializer />
        <Router future={{ v7_startTransition: true, v7_relativeSplatPath: true }}>
          <AuthGuard>
            <AppRoutes />
          </AuthGuard>
        </Router>
      </AntApp>
    </ConfigProvider>
  )
}

const App: React.FC = () => (
  <ThemeProvider>
    <AppContent />
  </ThemeProvider>
)

export default App
