import { useEffect } from 'react'
import { Button, Col, Form, Input, Modal, Row, Space, Switch } from 'antd'
import type { User } from '@/types'
import type { UserEditValues } from './types'

interface Props {
  open: boolean
  target: User | null
  canChangeRole: boolean
  onCancel: () => void
  onSubmit: (target: User, values: UserEditValues) => Promise<void>
}

const EditFields = ({ canChangeRole }: { canChangeRole: boolean }) => (
  <>
    <Row gutter={16}>
      <Col span={12}>
        <Form.Item label="用户名" name="username" rules={[{ required: true, message: '请输入用户名' }, { min: 3, message: '用户名至少3个字符' }, { pattern: /^[a-zA-Z0-9_-]+$/, message: '用户名只能包含字母、数字、下划线和横线' }]}><Input /></Form.Item>
      </Col>
      <Col span={12}><Form.Item label="邮箱" name="email" rules={[{ type: 'email', message: '请输入正确的邮箱格式' }]}><Input /></Form.Item></Col>
    </Row>
    <Row gutter={16}>
      <Col span={12}><Form.Item label="账户状态" name="is_active" valuePropName="checked"><Switch checkedChildren="激活" unCheckedChildren="禁用" /></Form.Item></Col>
      <Col span={12}><Form.Item label="管理员权限" name="is_admin" valuePropName="checked"><Switch checkedChildren="是" unCheckedChildren="否" disabled={!canChangeRole} /></Form.Item></Col>
    </Row>
  </>
)

export const EditUserModal = ({ open, target, canChangeRole, onCancel, onSubmit }: Props) => {
  const [form] = Form.useForm<UserEditValues>()
  useEffect(() => {
    if (open && target) form.setFieldsValue({ username: target.username, email: target.email, is_active: target.is_active, is_admin: target.is_admin })
  }, [form, open, target])
  const submit = async (values: UserEditValues) => {
    if (!target) return
    await onSubmit(target, values)
    form.resetFields()
    onCancel()
  }
  const cancel = () => { form.resetFields(); onCancel() }
  return (
    <Modal title="编辑用户" open={open} onCancel={cancel} footer={null} width={600} forceRender>
      <Form form={form} layout="vertical" onFinish={submit}>
        <EditFields canChangeRole={canChangeRole} />
        <Form.Item><Space><Button type="primary" htmlType="submit">更新用户</Button><Button onClick={cancel}>取消</Button></Space></Form.Item>
      </Form>
    </Modal>
  )
}
