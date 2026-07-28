import type React from 'react'
import { BrowserRouter as Router, Routes, Route, Navigate } from 'react-router-dom'
import { App as AntApp, ConfigProvider, FloatButton, Spin } from 'antd'
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
import '@/styles/globals.css'
import '@/styles/variables.css'
import '@/styles/antd-fixes.css'

// Lazy-loaded pages
const Login = lazyLoad(() => import('@/pages/Login'))
const Dashboard = lazyLoad(() => import('@/pages/Dashboard'))
const Workers = lazyLoad(() => import('@/pages/Workers'))
const Projects = lazyLoad(() => import('@/pages/Projects'))
const Repositories = lazyLoad(() => import('@/pages/Repositories'))
const Tasks = lazyLoad(() => import('@/pages/Tasks'))
const CrawlBatchList = lazyLoad(() => import('@/pages/Crawl/BatchList'))

const Settings = lazyLoad(() => import('@/pages/Settings'))
const Envs = lazyLoad(() => import('@/pages/Envs'))
const UserManagement = lazyLoad(() => import('@/pages/UserManagement'))
const SystemConfig = lazyLoad(() => import('@/pages/SystemConfig'))
const TaskCreate = lazyLoad(() => import('@/pages/Tasks/TaskCreate'))
const TaskDetail = lazyLoad(() => import('@/pages/Tasks/TaskDetail'))
const TaskEdit = lazyLoad(() => import('@/pages/Tasks/TaskEdit'))
const ExecutionLogs = lazyLoad(() => import('@/pages/Tasks/ExecutionLogs'))
const AlertConfig = lazyLoad(() => import('@/pages/AlertConfig'))
const AuditLog = lazyLoad(() => import('@/pages/AuditLog'))

// 会话恢复期间的全屏加载态（替代空白页）
const RouteFallback: React.FC = () => (
  <div
    style={{
      minHeight: '100vh',
      display: 'flex',
      alignItems: 'center',
      justifyContent: 'center'
    }}
  >
    <Spin size="large" />
  </div>
)

// Protected route wrapper
const ProtectedRoute: React.FC<{ children: React.ReactNode }> = ({ children }) => {
  const { isAuthenticated, loading } = useAuth()

  if (loading) return <RouteFallback />
  if (!isAuthenticated) {
    return <Navigate to="/login" replace />
  }

  return <>{children}</>
}

// Public route wrapper - redirects authenticated users
const PublicRoute: React.FC<{ children: React.ReactNode }> = ({ children }) => {
  const { isAuthenticated, loading } = useAuth()
  if (loading) return <RouteFallback />
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
export const AppRoutes: React.FC = () => (
  <>
    <Routes>
      <Route path="/login" element={<PublicRoute><Login /></PublicRoute>} />
      <Route path="/" element={<ProtectedRoute><Layout /></ProtectedRoute>}>
        <Route index element={<Navigate to="/dashboard" replace />} />
        <Route path="dashboard" element={<Dashboard />} />
        <Route path="workers" element={<Workers />} />
        <Route path="projects/*" element={<Projects />} />
        <Route path="repositories" element={<Repositories />} />
        <Route path="envs" element={<Envs />} />
        <Route path="tasks" element={<Tasks />} />
        <Route path="tasks/create" element={<TaskCreate />} />
        <Route path="tasks/:id/edit" element={<TaskEdit />} />
        <Route path="tasks/:id" element={<TaskDetail />} />
        <Route path="tasks/:taskId/runs/:runId" element={<ExecutionLogs />} />
        <Route path="crawl-batches" element={<CrawlBatchList />} />

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
        <Router>
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
