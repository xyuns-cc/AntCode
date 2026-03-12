import type React from 'react'
import { useCallback, useEffect, useMemo, useState } from 'react'
import {
  Alert,
  Button,
  Card,
  Form,
  Input,
  Modal,
  Popconfirm,
  Select,
  Space,
  Table,
  Tag,
  Typography,
} from 'antd'
import type { ColumnsType } from 'antd/es/table'
import {
  DeleteOutlined,
  EditOutlined,
  PlusOutlined,
  ReloadOutlined,
} from '@ant-design/icons'
import type { GitCredential, GitCredentialCreateRequest, GitCredentialUpdateRequest } from '@/types'
import { gitCredentialService } from '@/services/gitCredentials'
import showNotification from '@/utils/notification'
import { formatDateTime } from '@/utils/format'

const { Text } = Typography

type ModalMode = 'create' | 'edit'

type GitCredentialFormValues = {
  name: string
  auth_type: 'token' | 'basic'
  username?: string
  secret?: string
  host_scope: string
}

const buildUsernameRules = (authType: GitCredentialFormValues['auth_type']) => {
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

const GitCredentialsTab: React.FC = () => {
  const [items, setItems] = useState<GitCredential[]>([])
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string>('')

  const [modalOpen, setModalOpen] = useState(false)
  const [modalMode, setModalMode] = useState<ModalMode>('create')
  const [current, setCurrent] = useState<GitCredential | null>(null)
  const [submitting, setSubmitting] = useState(false)

  const [form] = Form.useForm<GitCredentialFormValues>()
  const authTypeValue = Form.useWatch('auth_type', form) as GitCredentialFormValues['auth_type'] | undefined

  const load = useCallback(async () => {
    setLoading(true)
    setError('')
    try {
      const list = await gitCredentialService.listGitCredentials()
      setItems(list)
    } catch (e) {
      const messageText = e instanceof Error ? e.message : '加载 Git 凭证失败'
      setError(messageText)
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    load()
  }, [load])

  const openCreate = useCallback(() => {
    setModalMode('create')
    setCurrent(null)
    form.resetFields()
    form.setFieldsValue({
      auth_type: 'token',
    })
    setModalOpen(true)
  }, [form])

  const openEdit = useCallback((credential: GitCredential) => {
    setModalMode('edit')
    setCurrent(credential)
    form.resetFields()
    form.setFieldsValue({
      name: credential.name,
      auth_type: credential.auth_type,
      username: credential.username ?? undefined,
      host_scope: credential.host_scope,
      secret: undefined,
    })
    setModalOpen(true)
  }, [form])

  const handleDelete = useCallback(async (credential: GitCredential) => {
    try {
      await gitCredentialService.deleteGitCredential(credential.id)
      showNotification('success', '删除成功', `已删除凭证：${credential.name}`)
      await load()
    } catch (e) {
      const messageText = e instanceof Error ? e.message : '删除 Git 凭证失败'
      showNotification('error', '删除失败', messageText)
    }
  }, [load])

  const handleSubmit = async () => {
    try {
      const values = await form.validateFields()
      setSubmitting(true)

      if (modalMode === 'create') {
        if (!values.secret) {
          showNotification('error', '创建失败', '请填写密钥')
          return
        }
        const payload: GitCredentialCreateRequest = {
          name: values.name,
          auth_type: values.auth_type,
          username: typeof values.username === 'string' ? values.username.trim() : values.username,
          secret: values.secret,
          host_scope: values.host_scope,
        }
        await gitCredentialService.createGitCredential(payload)
        showNotification('success', '创建成功', 'Git 凭证已创建')
      } else {
        if (!current) return
        const payload: GitCredentialUpdateRequest = {
          name: values.name,
          auth_type: values.auth_type,
          username: typeof values.username === 'string' ? values.username.trim() : values.username,
          host_scope: values.host_scope,
          ...(values.secret ? { secret: values.secret } : {}),
        }
        await gitCredentialService.updateGitCredential(current.id, payload)
        showNotification('success', '更新成功', 'Git 凭证已更新')
      }

      setModalOpen(false)
      await load()
    } catch (e) {
      if (e instanceof Error && e.message) {
        // validateFields / 请求错误都由上层提示，这里不重复弹
      }
    } finally {
      setSubmitting(false)
    }
  }

  const columns: ColumnsType<GitCredential> = useMemo(() => [
    {
      title: '名称',
      dataIndex: 'name',
      key: 'name',
      render: (value: string) => <Text strong>{value}</Text>,
    },
    {
      title: 'Host 范围',
      dataIndex: 'host_scope',
      key: 'host_scope',
      render: (value: string) => <Text code>{value}</Text>,
    },
    {
      title: '认证类型',
      dataIndex: 'auth_type',
      key: 'auth_type',
      render: (value: GitCredential['auth_type']) => (
        <Tag color={value === 'basic' ? 'gold' : 'blue'}>{value}</Tag>
      ),
    },
    {
      title: '用户名',
      dataIndex: 'username',
      key: 'username',
      render: (value: string | null | undefined) => value || '-',
    },
    {
      title: '密钥',
      dataIndex: 'has_secret',
      key: 'has_secret',
      render: (value: boolean) => (
        <Tag color={value ? 'green' : 'default'}>{value ? '已设置' : '未设置'}</Tag>
      ),
    },
    {
      title: '更新时间',
      dataIndex: 'updated_at',
      key: 'updated_at',
      render: (value: string) => formatDateTime(value),
    },
    {
      title: '操作',
      key: 'actions',
      render: (_: unknown, record: GitCredential) => (
        <Space>
          <Button
            size="small"
            icon={<EditOutlined />}
            onClick={() => openEdit(record)}
          >
            编辑
          </Button>
          <Popconfirm
            title="确认删除该凭证？"
            description="删除后不可恢复，引用该凭证的项目可能无法拉取私有仓库。"
            onConfirm={() => handleDelete(record)}
            okText="删除"
            cancelText="取消"
          >
            <Button
              size="small"
              danger
              icon={<DeleteOutlined />}
            >
              删除
            </Button>
          </Popconfirm>
        </Space>
      ),
    },
  ], [handleDelete, openEdit])

  return (
    <Card
      title="Git 凭证管理"
      extra={(
        <Space>
          <Button icon={<ReloadOutlined />} onClick={load} loading={loading}>
            刷新
          </Button>
          <Button type="primary" icon={<PlusOutlined />} onClick={openCreate}>
            新增凭证
          </Button>
        </Space>
      )}
    >
      {error && (
        <Alert
          type="error"
          showIcon
          message="加载失败"
          description={error}
          style={{ marginBottom: 12 }}
        />
      )}

      <Table
        rowKey="id"
        columns={columns}
        dataSource={items}
        loading={loading}
        pagination={{ pageSize: 10 }}
      />

      <Modal
        title={modalMode === 'create' ? '新增 Git 凭证' : '编辑 Git 凭证'}
        open={modalOpen}
        onCancel={() => setModalOpen(false)}
        onOk={handleSubmit}
        confirmLoading={submitting}
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
            rules={buildUsernameRules(authTypeValue || 'token')}
            extra={authTypeValue === 'basic' ? 'Basic 认证必须填写仓库用户名' : undefined}
          >
            <Input placeholder="例如：your-username" />
          </Form.Item>

          <Form.Item
            label={modalMode === 'create' ? '密钥' : '新密钥（留空不修改）'}
            name="secret"
            rules={modalMode === 'create' ? [{ required: true, message: '请输入密钥' }] : []}
          >
            <Input.Password placeholder="Token 或密码" />
          </Form.Item>

          <Form.Item
            label="Host 范围"
            name="host_scope"
            rules={[{ required: true, message: '请输入 Host 范围' }]}
            extra="例如：github.com / gitlab.com"
          >
            <Input placeholder="github.com" />
          </Form.Item>
        </Form>
      </Modal>
    </Card>
  )
}

export default GitCredentialsTab
