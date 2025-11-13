import React, { useState, useEffect, useCallback } from 'react'
import {
  Card,
  Table,
  Button,
  Space,
  Modal,
  Form,
  Input,
  Switch,
  Tag,
  Popconfirm,
  Tooltip,
  Row,
  Col
} from 'antd'
import {
  PlusOutlined,
  EditOutlined,
  DeleteOutlined,
  ReloadOutlined,
  KeyOutlined,
  UserOutlined,
  TeamOutlined
} from '@ant-design/icons'
import type { ColumnsType } from 'antd/es/table'
import { useAuth } from '@/hooks/useAuth'
import apiClient from '@/services/api'
import type { User, ApiResponse } from '@/types'
import styles from './UserManagement.module.css'

interface UserFormData {
  username: string
  password: string
  email?: string
  is_active: boolean
  is_admin: boolean
}

interface PasswordFormData {
  new_password: string
  confirm_password: string
}

const INITIAL_PAGE = 1
const INITIAL_PAGE_SIZE = 20
const INITIAL_SORT_FIELD: 'id' | 'username' | 'created_at' | null = null
const INITIAL_SORT_ORDER: 'asc' | 'desc' = 'asc'

const UserManagement: React.FC = () => {
  const { user: currentUser } = useAuth()
  
  // 用户列表数据
  const [users, setUsers] = useState<User[]>([])
  const [loading, setLoading] = useState(false)
  const [pagination, setPagination] = useState({
    current: INITIAL_PAGE,
    pageSize: INITIAL_PAGE_SIZE,
    total: 0
  })
  const [sortField, setSortField] = useState<typeof INITIAL_SORT_FIELD>(INITIAL_SORT_FIELD)
  const [sortOrder, setSortOrder] = useState<typeof INITIAL_SORT_ORDER>(INITIAL_SORT_ORDER)

  // 表单状态
  const [createModalVisible, setCreateModalVisible] = useState(false)
  const [editModalVisible, setEditModalVisible] = useState(false)
  const [passwordModalVisible, setPasswordModalVisible] = useState(false)
  const [selectedUser, setSelectedUser] = useState<User | null>(null)
  const [createForm] = Form.useForm()
  const [editForm] = Form.useForm()
  const [passwordForm] = Form.useForm()

  const fetchUsers = useCallback(async ({
    page,
    size,
    sortField: sortFieldOverride,
    sortOrder: sortOrderOverride
  }: {
    page: number
    size: number
    sortField?: 'id' | 'username' | 'created_at' | null
    sortOrder?: 'asc' | 'desc'
  }) => {
    if (!currentUser?.is_admin) {
      return
    }

    setLoading(true)
    try {
      const sortFieldValue = sortFieldOverride ?? undefined
      const sortOrderValue = sortFieldOverride ? (sortOrderOverride ?? 'asc') : undefined

      const response = await apiClient.get<any>('/api/v1/users/', {
        params: {
          page,
          size,
          sort_by: sortFieldValue ?? undefined,
          sort_order: sortOrderValue
        }
      })

      const payload = response.data

      if (payload?.success) {
        const rawData = payload.data
        const paginationInfo = payload.pagination || rawData?.pagination

        const items = Array.isArray(rawData)
          ? rawData
          : Array.isArray(rawData?.items)
            ? rawData.items
            : []

        const total =
          typeof paginationInfo?.total === 'number'
            ? paginationInfo.total
            : typeof rawData?.total === 'number'
              ? rawData.total
              : items.length

        const respPage =
          typeof paginationInfo?.page === 'number'
            ? paginationInfo.page
            : typeof rawData?.page === 'number'
              ? rawData.page
              : page

        const respSize =
          typeof paginationInfo?.size === 'number'
            ? paginationInfo.size
            : typeof rawData?.size === 'number'
              ? rawData.size
              : size

        setUsers(items)
        setPagination({
          current: respPage,
          pageSize: respSize,
          total
        })
      }
    } catch (error: any) {
      console.error('获取用户列表失败:', error)
      setUsers([])
    } finally {
      setLoading(false)
    }
  }, [currentUser?.is_admin])

  // 处理排序
  const handleSort = (field: 'id' | 'username' | 'created_at') => {
    let nextField: typeof field | null = field
    let nextOrder: 'asc' | 'desc' = 'asc'

    if (sortField === field) {
      if (sortOrder === 'asc') {
        nextOrder = 'desc'
      } else {
        nextField = null
        nextOrder = 'asc'
      }
    } else {
      nextOrder = 'asc'
    }

    setSortField(nextField)
    setSortOrder(nextOrder)
    setPagination(prev => ({
      ...prev,
      current: 1
    }))
    fetchUsers({
      page: 1,
      size: pagination.pageSize,
      sortField: nextField,
      sortOrder: nextOrder
    })
  }

  // 处理分页变化
  const handlePaginationChange = (page: number, size: number) => {
    const normalizedSize = size || pagination.pageSize
    setPagination(prev => ({
      ...prev,
      current: page,
      pageSize: normalizedSize
    }))
    fetchUsers({
      page,
      size: normalizedSize,
      sortField,
      sortOrder
    })
  }

  // 创建用户
  const handleCreateUser = async (values: UserFormData) => {
    try {
      const response = await apiClient.post<ApiResponse<User>>('/api/v1/users/', values)
      
      if (response.data.success) {
        setCreateModalVisible(false)
        createForm.resetFields()
        setPagination(prev => ({
          ...prev,
          current: 1
        }))
        fetchUsers({
          page: 1,
          size: pagination.pageSize,
          sortField,
          sortOrder
        })
      }
    } catch (error: any) {
      console.error('创建用户失败:', error)
    }
  }

  // 更新用户
  const handleUpdateUser = async (values: Partial<UserFormData>) => {
    if (!selectedUser) return

    try {
      const response = await apiClient.put<ApiResponse<User>>(`/api/v1/users/${selectedUser.id}`, values)
      
      if (response.data.success) {
        setEditModalVisible(false)
        editForm.resetFields()
        setSelectedUser(null)
        fetchUsers({
          page: pagination.current,
          size: pagination.pageSize,
          sortField,
          sortOrder
        })
      }
    } catch (error: any) {
      console.error('更新用户失败:', error)
    }
  }

  // 重置密码
  const handleResetPassword = async (values: PasswordFormData) => {
    if (!selectedUser) return
    if (values.new_password !== values.confirm_password) {
      return
    }

    try {
      const response = await apiClient.put<ApiResponse>(`/api/v1/users/${selectedUser.id}/reset-password`, {
        new_password: values.new_password
      })
      
      if (response.data.success) {
        setPasswordModalVisible(false)
        passwordForm.resetFields()
        setSelectedUser(null)
      }
    } catch (error: any) {
      console.error('重置密码失败:', error)
    }
  }

  // 删除用户
  const handleDeleteUser = async (userId: number) => {
    try {
      const response = await apiClient.delete<ApiResponse>(`/api/v1/users/${userId}`)
      
      if (response.data.success) {
        const remainingUsers = users.filter(u => u.id !== userId)
        setUsers(remainingUsers)

        if (remainingUsers.length === 0 && pagination.current > 1) {
          const prevPage = pagination.current - 1
          setPagination(prev => ({
            ...prev,
            current: prevPage
          }))
          fetchUsers({
            page: prevPage,
            size: pagination.pageSize,
            sortField,
            sortOrder
          })
        } else {
          fetchUsers({
            page: pagination.current,
            size: pagination.pageSize,
            sortField,
            sortOrder
          })
        }
      }
    } catch (error: any) {
      console.error('删除用户失败:', error)
    }
  }

  // 表格列配置
  const columns: ColumnsType<User> = [
    {
      title: () => (
        <span 
          style={{ cursor: 'pointer' }} 
          onClick={() => handleSort('id')}
        >
          ID {sortField === 'id' && (sortOrder === 'asc' ? '↑' : '↓')}
        </span>
      ),
      dataIndex: 'id',
      key: 'id',
      width: 80
    },
    {
      title: () => (
        <span 
          style={{ cursor: 'pointer' }} 
          onClick={() => handleSort('username')}
        >
          用户名 {sortField === 'username' && (sortOrder === 'asc' ? '↑' : '↓')}
        </span>
      ),
      dataIndex: 'username',
      key: 'username',
      render: (text: string, record: User) => (
        <Space>
          <UserOutlined />
          <span>{text}</span>
          {record.is_admin && <Tag color="gold" icon={<TeamOutlined />}>管理员</Tag>}
        </Space>
      )
    },
    {
      title: '邮箱',
      dataIndex: 'email',
      key: 'email',
      render: (email: string) => email || '-'
    },
    {
      title: '状态',
      dataIndex: 'is_active',
      key: 'is_active',
      render: (isActive: boolean) => (
        <Tag color={isActive ? 'success' : 'error'}>
          {isActive ? '激活' : '禁用'}
        </Tag>
      )
    },
    {
      title: () => (
        <span 
          style={{ cursor: 'pointer' }} 
          onClick={() => handleSort('created_at')}
        >
          创建时间 {sortField === 'created_at' && (sortOrder === 'asc' ? '↑' : '↓')}
        </span>
      ),
      dataIndex: 'created_at',
      key: 'created_at',
      render: (date: string) => new Date(date).toLocaleString()
    },
    {
      title: '最后登录',
      dataIndex: 'last_login_at',
      key: 'last_login_at',
      render: (date: string) => date ? new Date(date).toLocaleString() : '从未登录'
    },
    {
      title: '操作',
      key: 'actions',
      width: 200,
      render: (_, record: User) => (
        <Space>
          <Tooltip title="编辑用户">
            <Button
              type="text"
              icon={<EditOutlined />}
              onClick={() => {
                setSelectedUser(record)
                editForm.setFieldsValue({
                  username: record.username,
                  email: record.email,
                  is_active: record.is_active,
                  is_admin: record.is_admin
                })
                setEditModalVisible(true)
              }}
            />
          </Tooltip>
          <Tooltip title="重置密码">
            <Button
              type="text"
              icon={<KeyOutlined />}
              onClick={() => {
                setSelectedUser(record)
                setPasswordModalVisible(true)
              }}
            />
          </Tooltip>
          {record.id !== currentUser?.id && (
            <Popconfirm
              title="确认删除"
              description={`确定要删除用户 "${record.username}" 吗？此操作不可恢复。`}
              onConfirm={() => handleDeleteUser(record.id)}
              okText="确定"
              cancelText="取消"
            >
              <Tooltip title="删除用户">
                <Button
                  type="text"
                  danger
                  icon={<DeleteOutlined />}
                />
              </Tooltip>
            </Popconfirm>
          )}
        </Space>
      )
    }
  ]

  // 页面加载时获取数据
  useEffect(() => {
    if (currentUser?.is_admin) {
      fetchUsers({
        page: INITIAL_PAGE,
        size: INITIAL_PAGE_SIZE,
        sortField: INITIAL_SORT_FIELD,
        sortOrder: INITIAL_SORT_ORDER
      })
    }
  }, [currentUser?.is_admin, fetchUsers])

  // 权限检查
  if (!currentUser?.is_admin) {
    return (
      <div className={styles.accessDenied}>
        <Card>
          <div style={{ textAlign: 'center', padding: '2rem' }}>
            <TeamOutlined style={{ fontSize: '4rem', color: '#ff7875' }} />
            <h3>权限不足</h3>
            <p>只有管理员才能访问用户管理页面</p>
          </div>
        </Card>
      </div>
    )
  }

  return (
    <div className={styles.container}>
      <Card 
        title={
          <Space>
            <TeamOutlined />
            <span>用户管理</span>
          </Space>
        }
        extra={
          <Space>
            <Button
              icon={<ReloadOutlined />}
              onClick={() => fetchUsers({
                page: pagination.current,
                size: pagination.pageSize,
                sortField,
                sortOrder
              })}
              loading={loading}
            >
              刷新
            </Button>
            <Button
              type="primary"
              icon={<PlusOutlined />}
              onClick={() => setCreateModalVisible(true)}
            >
              添加用户
            </Button>
          </Space>
        }
      >
        <Table
          columns={columns}
        dataSource={users}
          rowKey="id"
          loading={loading}
          pagination={{
          current: pagination.current,
          pageSize: pagination.pageSize,
          total: pagination.total,
            showSizeChanger: true,
            showQuickJumper: true,
            showTotal: (total, range) => `第 ${range[0]}-${range[1]} 条，共 ${total} 条`,
          onChange: (page, size) => handlePaginationChange(page, size || pagination.pageSize),
            onShowSizeChange: (current, size) => handlePaginationChange(1, size)
          }}
        />
      </Card>

      {/* 创建用户模态框 */}
      <Modal
        title={
          <Space>
            <UserOutlined />
            <span>添加用户</span>
          </Space>
        }
        open={createModalVisible}
        onCancel={() => {
          setCreateModalVisible(false)
          createForm.resetFields()
        }}
        footer={null}
        width={600}
        destroyOnHidden
        maskClosable={false}
      >
        <Form
          form={createForm}
          layout="vertical"
          onFinish={handleCreateUser}
        >
          <Row gutter={16}>
            <Col span={12}>
              <Form.Item
                label="用户名"
                name="username"
                rules={[
                  { required: true, message: '请输入用户名' },
                  { min: 3, message: '用户名至少3个字符' },
                  { pattern: /^[a-zA-Z0-9_-]+$/, message: '用户名只能包含字母、数字、下划线和横线' }
                ]}
              >
                <Input placeholder="请输入用户名" />
              </Form.Item>
            </Col>
            <Col span={12}>
              <Form.Item
                label="邮箱"
                name="email"
                rules={[
                  { type: 'email', message: '请输入正确的邮箱格式' }
                ]}
              >
                <Input placeholder="请输入邮箱（可选）" />
              </Form.Item>
            </Col>
          </Row>
          <Form.Item
            label="密码"
            name="password"
            rules={[
              { required: true, message: '请输入密码' },
              { min: 6, message: '密码至少6个字符' }
            ]}
          >
            <Input.Password placeholder="请输入密码" />
          </Form.Item>
          <Row gutter={16}>
            <Col span={12}>
              <Form.Item
                label="账户状态"
                name="is_active"
                valuePropName="checked"
                initialValue={true}
              >
                <Switch checkedChildren="激活" unCheckedChildren="禁用" />
              </Form.Item>
            </Col>
            <Col span={12}>
              <Form.Item
                label="管理员权限"
                name="is_admin"
                valuePropName="checked"
                initialValue={false}
              >
                <Switch checkedChildren="是" unCheckedChildren="否" />
              </Form.Item>
            </Col>
          </Row>
          <Form.Item style={{ marginBottom: 0, marginTop: 24 }}>
            <Space style={{ width: '100%', justifyContent: 'flex-end' }}>
              <Button onClick={() => {
                setCreateModalVisible(false)
                createForm.resetFields()
              }}>
                取消
              </Button>
              <Button type="primary" htmlType="submit" icon={<PlusOutlined />}>
                创建用户
              </Button>
            </Space>
          </Form.Item>
        </Form>
      </Modal>

      {/* 编辑用户模态框 */}
      <Modal
        title="编辑用户"
        open={editModalVisible}
        onCancel={() => {
          setEditModalVisible(false)
          setSelectedUser(null)
          editForm.resetFields()
        }}
        footer={null}
        width={600}
      >
        <Form
          form={editForm}
          layout="vertical"
          onFinish={handleUpdateUser}
        >
          <Row gutter={16}>
            <Col span={12}>
              <Form.Item
                label="用户名"
                name="username"
                rules={[
                  { required: true, message: '请输入用户名' },
                  { min: 3, message: '用户名至少3个字符' }
                ]}
              >
                <Input placeholder="请输入用户名" disabled />
              </Form.Item>
            </Col>
            <Col span={12}>
              <Form.Item
                label="邮箱"
                name="email"
                rules={[
                  { type: 'email', message: '请输入正确的邮箱格式' }
                ]}
              >
                <Input placeholder="请输入邮箱（可选）" />
              </Form.Item>
            </Col>
          </Row>
          <Row gutter={16}>
            <Col span={12}>
              <Form.Item
                label="账户状态"
                name="is_active"
                valuePropName="checked"
              >
                <Switch checkedChildren="激活" unCheckedChildren="禁用" />
              </Form.Item>
            </Col>
            <Col span={12}>
              <Form.Item
                label="管理员权限"
                name="is_admin"
                valuePropName="checked"
              >
                <Switch 
                  checkedChildren="是" 
                  unCheckedChildren="否"
                  disabled={selectedUser?.id === currentUser?.id}
                />
              </Form.Item>
            </Col>
          </Row>
          {selectedUser?.id === currentUser?.id && (
            <div style={{ marginBottom: 16, color: '#faad14' }}>
              <span>注意：不能修改自己的管理员权限</span>
            </div>
          )}
          <Form.Item>
            <Space>
              <Button type="primary" htmlType="submit">
                更新用户
              </Button>
              <Button onClick={() => {
                setEditModalVisible(false)
                setSelectedUser(null)
                editForm.resetFields()
              }}>
                取消
              </Button>
            </Space>
          </Form.Item>
        </Form>
      </Modal>

      {/* 重置密码模态框 */}
      <Modal
        title="重置密码"
        open={passwordModalVisible}
        onCancel={() => {
          setPasswordModalVisible(false)
          setSelectedUser(null)
          passwordForm.resetFields()
        }}
        footer={null}
        width={400}
      >
        <Form
          form={passwordForm}
          layout="vertical"
          onFinish={handleResetPassword}
        >
          <Form.Item
            label="新密码"
            name="new_password"
            rules={[
              { required: true, message: '请输入新密码' },
              { min: 6, message: '密码至少6个字符' }
            ]}
          >
            <Input.Password placeholder="请输入新密码" />
          </Form.Item>
          <Form.Item
            label="确认密码"
            name="confirm_password"
            dependencies={['new_password']}
            rules={[
              { required: true, message: '请确认新密码' },
              ({ getFieldValue }) => ({
                validator(_, value) {
                  if (!value || getFieldValue('new_password') === value) {
                    return Promise.resolve()
                  }
                  return Promise.reject(new Error('两次输入的密码不一致'))
                }
              })
            ]}
          >
            <Input.Password placeholder="请再次输入新密码" />
          </Form.Item>
          <Form.Item>
            <Space>
              <Button type="primary" htmlType="submit">
                重置密码
              </Button>
              <Button onClick={() => {
                setPasswordModalVisible(false)
                setSelectedUser(null)
                passwordForm.resetFields()
              }}>
                取消
              </Button>
            </Space>
          </Form.Item>
        </Form>
      </Modal>
    </div>
  )
}

export default UserManagement
