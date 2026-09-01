import type React from 'react'
import { useEffect } from 'react'
import { Form, Input, Button, Card, Checkbox, ConfigProvider } from 'antd'
import { UserOutlined, LockOutlined } from '@ant-design/icons'
import { useNavigate } from 'react-router'
import { useAuth } from '@/hooks/useAuth'
import { AuthHandler } from '@/utils/authHandler'
import { STORAGE_KEYS } from '@/utils/constants'
import { validationRules } from '@/utils/validators'
import Logger from '@/utils/logger'
import { useBrandingStore } from '@/stores/brandingStore'
import type { LoginRequest } from '@/types'
import styles from './Login.module.css'

// P1-07: 已移除 SecureStorage import 与 isValidRememberedPassword —— 密码不再持久化,
// 相关"加密"是 XOR + 公开设备指纹派生 key,无法抵御 XSS/扩展/日志系统。
const isValidRememberedUsername = (value: string): boolean => {
  return /^[a-zA-Z0-9_]{3,32}$/.test(value)
}

// 登录页面专用主题配置，覆盖深色主题的输入框样式
const loginTheme = {
  components: {
    Input: {
      colorBgContainer: 'rgba(255, 255, 255, 0.1)',
      colorBgContainerDisabled: 'rgba(255, 255, 255, 0.05)',
      colorBorder: 'rgba(255, 255, 255, 0.2)',
      colorText: '#ffffff',
      colorTextPlaceholder: 'rgba(255, 255, 255, 0.6)',
      colorIcon: 'rgba(255, 255, 255, 0.6)',
      colorIconHover: 'rgba(255, 255, 255, 0.9)',
      activeBorderColor: 'rgba(255, 255, 255, 0.4)',
      hoverBorderColor: 'rgba(255, 255, 255, 0.3)',
      activeShadow: '0 0 0 2px rgba(255, 255, 255, 0.1)',
    },
  },
}

const Login: React.FC = () => {
  const navigate = useNavigate()
  const { login, loading, isAuthenticated } = useAuth()
  const branding = useBrandingStore((state) => state.branding)
  const [form] = Form.useForm()

  useEffect(() => {
    if (isAuthenticated) {
      navigate(AuthHandler.getRedirectPath(), { replace: true })
      return undefined
    }

    // P1-07: 只自动填用户名,**不再持久化密码**。
    // 密码用 XOR + 公开设备指纹派生 key "加密"没有任何真正安全性,
    // XSS/扩展/日志系统均可离线还原,直接删除该行为。
    const rememberMe = localStorage.getItem(STORAGE_KEYS.REMEMBER_ME) === 'true'
    if (!rememberMe) {
      return undefined
    }

    const username = (localStorage.getItem(STORAGE_KEYS.REMEMBER_USERNAME) || '').trim()

    if (!isValidRememberedUsername(username)) {
      localStorage.removeItem(STORAGE_KEYS.REMEMBER_ME)
      localStorage.removeItem(STORAGE_KEYS.REMEMBER_USERNAME)
      return undefined
    }

    const setFormValues = () => {
      const touched = form.isFieldsTouched(['username'], true)
      if (touched) {
        return
      }

      const currentValues = form.getFieldsValue(['username']) as {
        username?: string
      }
      const currentUsername = (currentValues.username || '').trim()

      if (currentUsername && currentUsername !== username) {
        return
      }

      form.setFieldsValue({ username, remember: true })
    }

    setFormValues()

    const timeouts = [50, 200, 500].map(delay => setTimeout(setFormValues, delay))

    return () => timeouts.forEach(clearTimeout)
  }, [isAuthenticated, navigate, form])

  const handleSubmit = async (values: LoginRequest & { remember: boolean }) => {
    try {
      // P1-07: 记住我 = 只记用户名,**不记密码**
      if (values.remember) {
        localStorage.setItem(STORAGE_KEYS.REMEMBER_ME, 'true')
        localStorage.setItem(STORAGE_KEYS.REMEMBER_USERNAME, values.username)
      } else {
        localStorage.removeItem(STORAGE_KEYS.REMEMBER_ME)
        localStorage.removeItem(STORAGE_KEYS.REMEMBER_USERNAME)
      }
      await login({ username: values.username.trim(), password: values.password })
      navigate(AuthHandler.getRedirectPath(), { replace: true })
    } catch (error) {
      Logger.error('Login failed:', error)
    }
  }

  return (
    <div className={styles.loginContainer}>
      <div className={styles.loginBox}>
        <div className={styles.header}>
          <h1 className={styles.title}>{branding.appTitle}</h1>
          <p className={styles.subtitle}>欢迎回来，请登录您的账户</p>
        </div>

        <Card className={styles.loginCard} variant="borderless">
          <ConfigProvider theme={loginTheme}>
            <Form form={form} name="login" onFinish={handleSubmit} autoComplete="off" size="large" layout="vertical">
              <Form.Item name="username" label="用户名" rules={[{ required: true, message: '请输入用户名' }, { min: 3, message: '用户名至少3个字符' }]}>
                <Input prefix={<UserOutlined />} placeholder="请输入用户名" autoComplete="off" />
              </Form.Item>

              <Form.Item name="password" label="密码" rules={validationRules.password}>
                <Input.Password prefix={<LockOutlined />} placeholder="请输入密码" autoComplete="new-password" />
              </Form.Item>

              <Form.Item>
                <div className={styles.formOptions}>
                  <Form.Item name="remember" valuePropName="checked" noStyle>
                    <Checkbox>记住我</Checkbox>
                  </Form.Item>
                </div>
              </Form.Item>

              <Form.Item>
                <Button type="primary" htmlType="submit" loading={loading} block className={styles.loginButton}>登录</Button>
              </Form.Item>
            </Form>
          </ConfigProvider>
        </Card>

        <div className={styles.footer}>
          <p>&copy; 2025 {branding.appTitle}. All rights reserved.</p>
        </div>
      </div>
    </div>
  )
}

export default Login
