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

  // 如果已登录，重定向到目标页面
  useEffect(() => {
    if (isAuthenticated) {
      const redirectPath = AuthHandler.getRedirectPath()
      navigate(redirectPath, { replace: true })
    }
  }, [isAuthenticated, navigate])

  // 处理表单提交
  const handleSubmit = async (values: LoginRequest & { remember: boolean }) => {
    try {
      await login({
        username: values.username,
        password: values.password
      })

      // 登录成功后跳转到目标页面
      const redirectPath = AuthHandler.getRedirectPath()
      navigate(redirectPath, { replace: true })
    } catch (error) {
      // 错误处理已在useAuth中完成
      Logger.error('Login failed:', error)
    }
  }

  // 处理第三方登录 - 移除
  // const handleOAuthLogin = (provider: string) => {
  //   // 这里可以实现第三方登录逻辑
  //   // TODO: 实现第三方登录
  // }

  return (
    <div className={styles.loginContainer}>
      <div className={styles.loginBox}>
        {/* Logo和标题 */}
        <div className={styles.header}>
          <div className={styles.logo}>
            <div className={styles.logoIcon}>A</div>
          </div>
          <h1 className={styles.title}>{APP_TITLE}</h1>
          <p className={styles.subtitle}>欢迎回来，请登录您的账户</p>
        </div>

        {/* 登录表单 */}
        <Card className={styles.loginCard} variant="borderless">
          <Form
            form={form}
            name="login"
            onFinish={handleSubmit}
            autoComplete="off"
            size="large"
            layout="vertical"
          >
            <Form.Item
              name="username"
              label="用户名"
              rules={[
                { required: true, message: '请输入用户名' },
                { min: 3, message: '用户名至少3个字符' }
              ]}
            >
              <Input
                prefix={<UserOutlined />}
                placeholder="请输入用户名"
                autoComplete="username"
              />
            </Form.Item>

            <Form.Item
              name="password"
              label="密码"
              rules={validationRules.password}
            >
              <Input.Password
                prefix={<LockOutlined />}
                placeholder="请输入密码"
                autoComplete="current-password"
              />
            </Form.Item>

            <Form.Item>
              <div className={styles.formOptions}>
                <Form.Item name="remember" valuePropName="checked" noStyle>
                  <Checkbox>记住我</Checkbox>
                </Form.Item>
                <Link to="/forgot-password" className={styles.forgotLink}>
                  忘记密码？
                </Link>
              </div>
            </Form.Item>

            <Form.Item>
              <Button
                type="primary"
                htmlType="submit"
                loading={loading}
                block
                className={styles.loginButton}
              >
                登录
              </Button>
            </Form.Item>
          </Form>
        </Card>

        {/* 页脚 */}
        <div className={styles.footer}>
          <p>&copy; 2024 {APP_TITLE}. All rights reserved.</p>
        </div>
      </div>
    </div>
  )
}

export default Login
