import { useEffect } from 'react'
import { Button, Col, Form, Input, Modal, Row, Space, Switch } from 'antd'
import { PlusOutlined, UserOutlined } from '@ant-design/icons'
import type { UserCreateValues } from './types'
import { passwordRules } from './passwordRules'

interface Props {
  open: boolean
  canCreateAdmin: boolean
  onCancel: () => void
  onSubmit: (values: UserCreateValues) => Promise<void>
}

const CreateFields = ({ canCreateAdmin }: { canCreateAdmin: boolean }) => (
  <>
    <Row gutter={16}>
      <Col span={12}>
        <Form.Item label="用户名" name="username" rules={[{ required: true, message: '请输入用户名' }, { min: 3, message: '用户名至少3个字符' }, { pattern: /^[a-zA-Z0-9_-]+$/, message: '用户名只能包含字母、数字、下划线和横线' }]}>
          <Input placeholder="请输入用户名" />
        </Form.Item>
      </Col>
      <Col span={12}>
        <Form.Item label="邮箱" name="email" rules={[{ type: 'email', message: '请输入正确的邮箱格式' }]}>
          <Input placeholder="请输入邮箱（可选）" />
        </Form.Item>
      </Col>
    </Row>
    <Form.Item label="密码" name="password" rules={passwordRules}><Input.Password placeholder="请输入密码" /></Form.Item>
    <Row gutter={16}>
      <Col span={12}><Form.Item label="账户状态" name="is_active" valuePropName="checked"><Switch checkedChildren="激活" unCheckedChildren="禁用" /></Form.Item></Col>
      <Col span={12}><Form.Item label="管理员权限" name="is_admin" valuePropName="checked" hidden={!canCreateAdmin}><Switch checkedChildren="是" unCheckedChildren="否" /></Form.Item></Col>
    </Row>
  </>
)

export const CreateUserModal = ({ open, canCreateAdmin, onCancel, onSubmit }: Props) => {
  const [form] = Form.useForm<UserCreateValues>()
  useEffect(() => { if (open) form.setFieldsValue({ is_active: true, is_admin: false }) }, [form, open])
  const submit = async (values: UserCreateValues) => {
    await onSubmit(values)
    form.resetFields()
    onCancel()
  }
  const cancel = () => { form.resetFields(); onCancel() }
  return (
    <Modal title={<Space><UserOutlined /><span>添加用户</span></Space>} open={open} onCancel={cancel} footer={null} width={600} destroyOnHidden maskClosable={false} forceRender>
      <Form form={form} layout="vertical" onFinish={submit} initialValues={{ is_active: true, is_admin: false }}>
        <CreateFields canCreateAdmin={canCreateAdmin} />
        <Form.Item style={{ marginBottom: 0, marginTop: 24 }}>
          <Space style={{ width: '100%', justifyContent: 'flex-end' }}><Button onClick={cancel}>取消</Button><Button type="primary" htmlType="submit" icon={<PlusOutlined />}>创建用户</Button></Space>
        </Form.Item>
      </Form>
    </Modal>
  )
}
