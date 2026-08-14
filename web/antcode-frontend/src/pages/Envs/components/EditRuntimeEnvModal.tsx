import type React from 'react'
import { useEffect, useState } from 'react'
import { Modal, Form, Input, Tag, Space, Typography, App } from 'antd'
import {
  RUNTIME_DESCRIPTION_MAX_BYTES,
  RUNTIME_KEY_MAX_BYTES,
  runtimeService,
  validateRuntimeMetadata,
} from '@/services/runtimes'
import type { EditRuntimeEnvModalProps } from '../types'

const { Text } = Typography

const metadataByteRule = (field: 'key' | 'description') => ({
  validator: (_rule: unknown, value?: string) => {
    validateRuntimeMetadata({ [field]: value })
    return Promise.resolve()
  },
})

const EditRuntimeEnvModal: React.FC<EditRuntimeEnvModalProps> = ({
  open,
  env,
  onClose,
  onSuccess,
}) => {
  const { message } = App.useApp()
  const [form] = Form.useForm()
  const [loading, setLoading] = useState(false)

  useEffect(() => {
    if (open && env) {
      form.setFieldsValue({
        key: env.key || '',
        description: env.description || '',
      })
    }
  }, [open, env, form])

  const handleSubmit = async () => {
    try {
      const values = await form.validateFields()
      setLoading(true)
      if (env?.workerId && env.envName) {
        await runtimeService.updateEnv(env.workerId, env.envName, {
          key: values.key || undefined,
          description: values.description || undefined,
        })
        message.success('Worker 环境更新成功')
        onSuccess()
        form.resetFields()
      }
    } catch (error: unknown) {
      const errMsg = error instanceof Error ? error.message : '更新失败'
      message.error(errMsg)
    } finally {
      setLoading(false)
    }
  }

  return (
    <Modal
      title={`编辑 Worker 环境 - ${env?.workerName || ''}`}
      open={open}
      onCancel={onClose}
      onOk={handleSubmit}
      confirmLoading={loading}
      okText="保存"
      cancelText="取消"
      forceRender
    >
      <Form form={form} layout="vertical" autoComplete="off">
        <Form.Item
          label="环境标识"
          name="key"
          help={`环境的别名或标识，最多 ${RUNTIME_KEY_MAX_BYTES} UTF-8 字节`}
          rules={[metadataByteRule('key')]}
        >
          <Input placeholder="输入标识，留空则清除标识" allowClear />
        </Form.Item>
        <Form.Item
          label="描述"
          name="description"
          help={`环境描述信息，最多 ${RUNTIME_DESCRIPTION_MAX_BYTES} UTF-8 字节`}
          rules={[metadataByteRule('description')]}
        >
          <Input.TextArea placeholder="输入环境描述" rows={3} allowClear />
        </Form.Item>
        {env && (
          <div style={{ marginTop: 16 }}>
            <Text type="secondary">当前环境：</Text>
            <div style={{ marginTop: 8 }}>
              <Space>
                <Tag color="cyan">{env.workerName}</Tag>
                <Text>Python {env.version}</Text>
                {env.current_project_id && (
                  <Tag color="blue">项目 ID: {env.current_project_id}</Tag>
                )}
              </Space>
            </div>
          </div>
        )}
      </Form>
    </Modal>
  )
}

export default EditRuntimeEnvModal
