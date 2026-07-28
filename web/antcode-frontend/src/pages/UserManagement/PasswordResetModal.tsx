import { Button, Form, Input, Modal, Space } from 'antd'
import type { User } from '@/types'
import type { PasswordResetValues } from './types'
import { passwordRules } from './passwordRules'

interface Props {
  open: boolean
  target: User | null
  onCancel: () => void
  onSubmit: (target: User, password: string) => Promise<void>
}

export const PasswordResetModal = ({ open, target, onCancel, onSubmit }: Props) => {
  const [form] = Form.useForm<PasswordResetValues>()
  const submit = async (values: PasswordResetValues) => {
    if (!target) return
    await onSubmit(target, values.new_password)
    form.resetFields()
    onCancel()
  }
  const cancel = () => { form.resetFields(); onCancel() }
  return (
    <Modal title="重置密码" open={open} onCancel={cancel} footer={null} width={400} forceRender>
      <Form form={form} layout="vertical" onFinish={submit}>
        <Form.Item label="新密码" name="new_password" rules={passwordRules}><Input.Password /></Form.Item>
        <Form.Item label="确认密码" name="confirm_password" dependencies={['new_password']} rules={[{ required: true, message: '请确认新密码' }, ({ getFieldValue }) => ({ validator: async (_, value) => { if (value !== getFieldValue('new_password')) throw new Error('两次输入的密码不一致') } })]}><Input.Password /></Form.Item>
        <Form.Item><Space><Button type="primary" htmlType="submit">重置密码</Button><Button onClick={cancel}>取消</Button></Space></Form.Item>
      </Form>
    </Modal>
  )
}
