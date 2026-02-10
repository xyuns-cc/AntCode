import React, { useState, memo } from 'react'
import { Card, Tabs, Form, Input, Button, Alert } from 'antd'
import { 
  UserOutlined, 
  MailOutlined, 
  LockOutlined, 
  SaveOutlined
} from '@ant-design/icons'
import { useAuth } from '@/hooks/useAuth'
import { authService } from '@/services/auth'
import type { UpdateUserRequest } from '@/types'
import styles from './Settings.module.css'

const Settings: React.FC = memo(() => {
  const { user, updateUser } = useAuth()
  const [emailForm] = Form.useForm()
  const [passwordForm] = Form.useForm()
  const [emailLoading, setEmailLoading] = useState(false)
  const [passwordLoading, setPasswordLoading] = useState(false)

  // 处理邮箱更新
  const handleEmailUpdate = async (values: { email: string }) => {
    setEmailLoading(true)
    try {
      const updateData: UpdateUserRequest = {
        email: values.email
      }
      await updateUser(updateData)
    } catch {
      // 错误已在useAuth中处理
    } finally {
      setEmailLoading(false)
    }
  }

  // 处理密码修改
  const handlePasswordChange = async (values: { 
    currentPassword: string
    newPassword: string
    confirmPassword: string 
  }) => {
    setPasswordLoading(true)
    try {
      await authService.changePassword(values.currentPassword, values.newPassword)
      passwordForm.resetFields()
    } catch {
      // 错误提示由拦截器统一处理
    } finally {
      setPasswordLoading(false)
    }
  }

  // 邮箱表单
  const EmailSettings = () => (
    <Card title="邮箱设置" size="small" className={styles.formCard}>
      <Form
        form={emailForm}
        layout="vertical"
        onFinish={handleEmailUpdate}
        initialValues={{ email: user?.email || '' }}
      >
        <Form.Item
          label="邮箱地址"
          name="email"
          rules={[
            { type: 'email', message: '请输入有效的邮箱地址' },
            { required: true, message: '请输入邮箱地址' }
          ]}
        >
          <Input
            prefix={<MailOutlined />}
            placeholder="请输入邮箱地址"
            size="large"
          />
        </Form.Item>

        <Form.Item>
          <Button
            type="primary"
            htmlType="submit"
            loading={emailLoading}
            icon={<SaveOutlined />}
            size="large"
          >
            更新邮箱
          </Button>
        </Form.Item>
      </Form>
    </Card>
  )

  // 密码表单
  const PasswordSettings = () => (
    <Card title="密码设置" size="small" className={styles.formCard}>
      <Alert
        message="密码安全提示"
        description="为了账户安全，请定期更换密码。新密码应包含字母、数字，长度至少6位。"
        type="info"
        showIcon
        className={styles.securityAlert}
      />

      <Form
        form={passwordForm}
        layout="vertical"
        onFinish={handlePasswordChange}
      >
        <Form.Item
          label="当前密码"
          name="currentPassword"
          rules={[
            { required: true, message: '请输入当前密码' }
          ]}
        >
          <Input.Password
            prefix={<LockOutlined />}
            placeholder="请输入当前密码"
            size="large"
          />
        </Form.Item>

        <Form.Item
          label="新密码"
          name="newPassword"
          rules={[
            { required: true, message: '请输入新密码' },
            { min: 6, message: '密码长度至少6位' },
            { max: 100, message: '密码长度不能超过100位' }
          ]}
        >
          <Input.Password
            prefix={<LockOutlined />}
            placeholder="请输入新密码"
            size="large"
          />
        </Form.Item>

        <Form.Item
          label="确认新密码"
          name="confirmPassword"
          dependencies={['newPassword']}
          rules={[
            { required: true, message: '请确认新密码' },
            ({ getFieldValue }) => ({
              validator(_, value) {
                if (!value || getFieldValue('newPassword') === value) {
                  return Promise.resolve()
                }
                return Promise.reject(new Error('两次输入的密码不一致'))
              },
            }),
          ]}
        >
          <Input.Password
            prefix={<LockOutlined />}
            placeholder="请再次输入新密码"
            size="large"
          />
        </Form.Item>

        <Form.Item>
          <Button
            type="primary"
            htmlType="submit"
            loading={passwordLoading}
            icon={<SaveOutlined />}
            size="large"
          >
            修改密码
          </Button>
        </Form.Item>
      </Form>
    </Card>
  )

  return (
    <div className={styles.settingsContainer}>
      <div className={styles.pageHeader}>
        <h1 className={styles.pageTitle}>
          <UserOutlined style={{ marginRight: 8 }} />
          用户设置
        </h1>
        <p className={styles.pageDescription}>
          管理您的账户信息和安全设置
        </p>
      </div>

      <Tabs 
        defaultActiveKey="email" 
        size="large" 
        className={styles.tabsContainer}
        items={[
          {
            key: 'email',
            label: (
              <span>
                <MailOutlined />
                邮箱设置
              </span>
            ),
            children: <EmailSettings />
          },
          {
            key: 'password',
            label: (
              <span>
                <LockOutlined />
                密码设置
              </span>
            ),
            children: <PasswordSettings />
          }
        ]}
      />
    </div>
  )
})

export default Settings
