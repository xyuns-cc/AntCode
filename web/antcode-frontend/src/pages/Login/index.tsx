import React, { useEffect } from 'react'
import { Form, Input, Button, Card, Checkbox } from 'antd'
import { UserOutlined, LockOutlined } from '@ant-design/icons'
import { useNavigate, Link } from 'react-router-dom'
import { useAuth } from '@/hooks/useAuth'
import { AuthHandler } from '@/utils/authHandler'
import { APP_TITLE } from '@/utils/constants'
import { validationRules } from '@/utils/validators'
import Logger from '@/utils/logger'
import type { LoginRequest } from '@/types'
import styles from './Login.module.css'

const Login: React.FC = () => {
  const navigate = useNavigate()
  const { login, loading, isAuthenticated } = useAuth()
  const [form] = Form.useForm()

  useEffect(() => {
    if (isAuthenticated) {
      navigate(AuthHandler.getRedirectPath(), { replace: true })
    }
  }, [isAuthenticated, navigate])

  const handleSubmit = async (values: LoginRequest & { remember: boolean }) => {
    try {
      await login({ username: values.username, password: values.password })
      navigate(AuthHandler.getRedirectPath(), { replace: true })
    } catch (error) {
      Logger.error('Login failed:', error)
    }
  }

  return (
    <div className={styles.loginContainer}>
      <div className={styles.loginBox}>
        <div className={styles.header}>
          <div className={styles.logo}>
            <div className={styles.logoIcon}>A</div>
          </div>
          <h1 className={styles.title}>{APP_TITLE}</h1>
          <p className={styles.subtitle}>欢迎回来，请登录您的账户</p>
        </div>

        <Card className={styles.loginCard} variant="borderless">
          <Form form={form} name="login" onFinish={handleSubmit} autoComplete="off" size="large" layout="vertical">
            <Form.Item name="username" label="用户名" rules={[{ required: true, message: '请输入用户名' }, { min: 3, message: '用户名至少3个字符' }]}>
              <Input prefix={<UserOutlined />} placeholder="请输入用户名" autoComplete="username" />
            </Form.Item>

            <Form.Item name="password" label="密码" rules={validationRules.password}>
              <Input.Password prefix={<LockOutlined />} placeholder="请输入密码" autoComplete="current-password" />
            </Form.Item>

            <Form.Item>
              <div className={styles.formOptions}>
                <Form.Item name="remember" valuePropName="checked" noStyle>
                  <Checkbox>记住我</Checkbox>
                </Form.Item>
                <Link to="/forgot-password" className={styles.forgotLink}>忘记密码？</Link>
              </div>
            </Form.Item>

            <Form.Item>
              <Button type="primary" htmlType="submit" loading={loading} block className={styles.loginButton}>登录</Button>
            </Form.Item>
          </Form>
        </Card>

        <div className={styles.footer}>
          <p>&copy; 2025 {APP_TITLE}. All rights reserved.</p>
        </div>
      </div>
    </div>
  )
}

export default Login
