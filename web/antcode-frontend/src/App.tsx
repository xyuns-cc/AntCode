import React, { useEffect } from 'react'
import { BrowserRouter as Router, Routes, Route, Navigate, useLocation } from 'react-router-dom'
import { ThemeProvider } from '@/contexts/ThemeContext'
import Layout from '@/components/common/Layout'
import AuthGuard from '@/components/common/AuthGuard'
import AdminRoute from '@/components/common/AdminRoute'
import { lazyLoad } from '@/utils/lazyLoad'
import { useAuth } from '@/hooks/useAuth'
import { useAuthStore } from '@/stores/authStore'
import { STORAGE_KEYS } from '@/utils/constants'
import { AuthHandler } from '@/utils/authHandler'
import { AlertProvider } from '@/components/common/AlertManager'
import AlertInitializer from '@/components/common/AlertInitializer'

// 懒加载页面组件
const Login = lazyLoad(() => import('@/pages/Login'))
const Dashboard = lazyLoad(() => import('@/pages/Dashboard'))
const Projects = lazyLoad(() => import('@/pages/Projects'))
const Tasks = lazyLoad(() => import('@/pages/Tasks'))
const Settings = lazyLoad(() => import('@/pages/Settings'))
const Envs = lazyLoad(() => import('@/pages/Envs'))
const UserManagement = lazyLoad(() => import('@/pages/UserManagement'))
const TaskCreate = lazyLoad(() => import('@/pages/Tasks/TaskCreate'))
const TaskDetail = lazyLoad(() => import('@/pages/Tasks/TaskDetail'))
const TaskEdit = lazyLoad(() => import('@/pages/Tasks/TaskEdit'))
const ExecutionLogs = lazyLoad(() => import('@/pages/Tasks/ExecutionLogs'))
const Monitor = lazyLoad(() => import('@/pages/Monitor'))
import '@/styles/globals.css'
import '@/styles/variables.css'
import '@/styles/antd-fixes.css'

// 路由认证检查组件
const RouteAuthChecker: React.FC = () => {
  const location = useLocation()
  const { isAuthenticated, clearUser } = useAuth()

  useEffect(() => {
    // 每次路由变化时检查认证状态
    const checkAuth = () => {
      const token = localStorage.getItem(STORAGE_KEYS.ACCESS_TOKEN)
      
      // 如果是受保护的路由且没有token
      if (location.pathname !== '/login' && !token) {
        if (isAuthenticated) {
          // 清除状态并处理认证失败
          clearUser()
          AuthHandler.handleAuthFailure(true)
        }
      }
    }

    // 延迟执行，确保路由完全加载
    const timeoutId = setTimeout(checkAuth, 100)
    return () => clearTimeout(timeoutId)
  }, [location.pathname, isAuthenticated, clearUser])

  return null
}

// 受保护的路由组件
const ProtectedRoute: React.FC<{ children: React.ReactNode }> = ({ children }) => {
  const { isAuthenticated } = useAuth()

  // 实时检查localStorage中的token
  const hasToken = localStorage.getItem(STORAGE_KEYS.ACCESS_TOKEN)

  // 如果Zustand状态显示已认证，但localStorage中没有token，说明token被外部清除了
  if (isAuthenticated && !hasToken) {
    // 立即清除认证状态并跳转
    const { clearUser } = useAuthStore.getState()
    clearUser()
    return <Navigate to="/login" replace />
  }

  if (!isAuthenticated || !hasToken) {
    return <Navigate to="/login" replace />
  }

  return <>{children}</>
}

// 公共路由组件（已登录用户不能访问）
const PublicRoute: React.FC<{ children: React.ReactNode }> = ({ children }) => {
  const { isAuthenticated } = useAuth()

  if (isAuthenticated) {
    return <Navigate to="/dashboard" replace />
  }

  return <>{children}</>
}

const App: React.FC = () => {
  return (
    <AlertProvider>
      <AlertInitializer />
      <ThemeProvider>
        <Router
          future={{
            v7_startTransition: true,
            v7_relativeSplatPath: true
          }}
        >
      <AuthGuard>
        <RouteAuthChecker />
        <Routes>
        {/* 公共路由 */}
        <Route
          path="/login"
          element={
            <PublicRoute>
              <Login />
            </PublicRoute>
          }
        />

        {/* 受保护的路由 */}
        <Route
          path="/"
          element={
            <ProtectedRoute>
              <Layout />
            </ProtectedRoute>
          }
        >
          <Route index element={<Navigate to="/dashboard" replace />} />
          <Route path="dashboard" element={<Dashboard />} />
          <Route path="monitor" element={<Monitor />} />
          <Route path="projects/*" element={<Projects />} />
          <Route path="envs" element={<Envs />} />
          <Route path="tasks" element={<Tasks />} />
          <Route path="tasks/create" element={<TaskCreate />} />
          <Route path="tasks/:id/edit" element={<TaskEdit />} />
          <Route path="tasks/:id" element={<TaskDetail />} />
          <Route path="tasks/:taskId/executions/:executionId" element={<ExecutionLogs />} />
          <Route path="user-management" element={
            <AdminRoute>
              <UserManagement />
            </AdminRoute>
          } />
          <Route path="settings" element={<Settings />} />
        </Route>

        {/* 404 页面 */}
        <Route path="*" element={<Navigate to="/dashboard" replace />} />
        </Routes>
      </AuthGuard>
    </Router>
  </ThemeProvider>
</AlertProvider>
  )
}

export default App
