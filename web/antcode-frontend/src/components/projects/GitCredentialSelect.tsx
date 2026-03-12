import type React from 'react'
import { useCallback, useEffect, useMemo, useState } from 'react'
import { Alert, Button, Form, Input, Modal, Select, Space, Typography } from 'antd'
import { PlusOutlined } from '@ant-design/icons'
import { gitCredentialService } from '@/services/gitCredentials'
import type { GitCredential, GitCredentialCreateRequest } from '@/types'
import Logger from '@/utils/logger'

const { Text } = Typography

const buildUsernameRules = (authType: GitCredentialCreateRequest['auth_type'] | undefined) => {
  if (authType !== 'basic') {
    return []
  }
  return [{
    validator: (_: unknown, value: string | undefined) => (
      typeof value === 'string' && value.trim()
        ? Promise.resolve()
        : Promise.reject(new Error('Basic 认证必须填写用户名'))
    )
  }]
}

interface GitCredentialSelectProps {
  value?: string
  onChange?: (value?: string) => void
  disabled?: boolean
}

const GitCredentialSelect: React.FC<GitCredentialSelectProps> = ({
  value,
  onChange,
  disabled = false,
}) => {
  const [items, setItems] = useState<GitCredential[]>([])
  const [loading, setLoading] = useState(false)
  const [loadError, setLoadError] = useState<string>('')
  const [createOpen, setCreateOpen] = useState(false)
  const [creating, setCreating] = useState(false)
  const [form] = Form.useForm<GitCredentialCreateRequest>()
  const authTypeValue = Form.useWatch('auth_type', form) as GitCredentialCreateRequest['auth_type'] | undefined

  const options = useMemo(
    () =>
      items.map((it) => ({
        label: `${it.name} (${it.host_scope})`,
        value: it.id,
      })),
    [items]
  )

  const load = useCallback(async () => {
    setLoading(true)
    setLoadError('')
    try {
      const list = await gitCredentialService.listGitCredentials()
      setItems(list)
    } catch (error) {
      Logger.error('加载 Git 凭证失败:', error)
      setLoadError('加载 Git 凭证失败')
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    load()
  }, [load])

  const handleCreate = async () => {
    try {
      const values = await form.validateFields()
      setCreating(true)
      const created = await gitCredentialService.createGitCredential(values)
      await load()
      onChange?.(created.id)
      setCreateOpen(false)
      form.resetFields()
    } catch (error) {
      Logger.error('创建 Git 凭证失败:', error)
    } finally {
      setCreating(false)
    }
  }

  return (
    <Space direction="vertical" style={{ width: '100%' }} size={8}>
      {loadError && (
        <Alert
          type="error"
          showIcon
          message={loadError}
          action={
            <Button size="small" onClick={load} disabled={loading}>
              重试
            </Button>
          }
        />
      )}

      <Space.Compact style={{ width: '100%' }}>
        <Select
          allowClear
          value={value === '' ? undefined : value}
          onChange={(v) => onChange?.(v ?? '')}
          options={options}
          loading={loading}
          disabled={disabled}
          placeholder="选择 Git 凭证"
          style={{ width: '100%' }}
        />
        <Button
          icon={<PlusOutlined />}
          onClick={() => setCreateOpen(true)}
          disabled={disabled}
          title="新建 Git 凭证"
        />
      </Space.Compact>

      <Modal
        title="新建 Git 凭证"
        open={createOpen}
        onCancel={() => setCreateOpen(false)}
        onOk={handleCreate}
        confirmLoading={creating}
        destroyOnClose
      >
        <Form form={form} layout="vertical">
          <Form.Item
            label="名称"
            name="name"
            rules={[{ required: true, message: '请输入名称' }]}
          >
            <Input placeholder="例如：公司 GitHub Token" />
          </Form.Item>

          <Form.Item
            label="认证类型"
            name="auth_type"
            initialValue="token"
            rules={[{ required: true, message: '请选择认证类型' }]}
          >
            <Select
              options={[
                { label: 'Token', value: 'token' },
                { label: 'Basic', value: 'basic' },
              ]}
            />
          </Form.Item>

          <Form.Item
            label="用户名"
            name="username"
            rules={buildUsernameRules(authTypeValue)}
            extra={authTypeValue === 'basic' ? <Text type="secondary">Basic 认证必须填写仓库用户名</Text> : undefined}
          >
            <Input placeholder="例如：your-username" />
          </Form.Item>

          <Form.Item
            label="密钥"
            name="secret"
            rules={[{ required: true, message: '请输入密钥' }]}
            extra={<Text type="secondary">仅用于后端克隆仓库，不会返回到前端</Text>}
          >
            <Input.Password placeholder="Token 或密码" />
          </Form.Item>

          <Form.Item
            label="Host 范围"
            name="host_scope"
            rules={[{ required: true, message: '请输入 Host 范围' }]}
            extra={<Text type="secondary">例如：github.com / gitlab.com</Text>}
          >
            <Input placeholder="github.com" />
          </Form.Item>
        </Form>
      </Modal>
    </Space>
  )
}

export default GitCredentialSelect
