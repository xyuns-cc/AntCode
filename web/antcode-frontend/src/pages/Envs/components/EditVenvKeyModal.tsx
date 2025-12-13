import React, { useEffect, useState } from 'react'
import { Modal, Form, Input, Tag, Space, Typography, App } from 'antd'
import envService from '@/services/envs'
import type { EditVenvKeyModalProps } from '../types'

const { Text } = Typography

const EditVenvKeyModal: React.FC<EditVenvKeyModalProps> = ({
  open,
  venv,
  onClose,
  onSuccess,
}) => {
  const { message } = App.useApp()
  const [form] = Form.useForm()
  const [loading, setLoading] = useState(false)

  useEffect(() => {
    if (open && venv) {
      form.setFieldsValue({
        key: venv.key || '',
        description: '',
      })
    }
  }, [open, venv, form])

  const handleSubmit = async () => {
    try {
      const values = await form.validateFields()
      setLoading(true)
      if (venv) {
        // 节点环境
        if (!venv.isLocal && venv.nodeId && venv.envName) {
          await envService.updateNodeEnv(venv.nodeId, venv.envName, {
            key: values.key || undefined,
            description: values.description || undefined,
          })
          message.success('节点环境更新成功')
        }
        // 本地环境（仅共享环境支持编辑）
        else if (venv.scope === 'shared') {
          await envService.updateSharedVenv(venv.id, { key: values.key || undefined })
          message.success('环境更新成功')
        }
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
      title={venv?.isLocal ? '编辑共享环境标识' : `编辑节点环境 - ${venv?.nodeName}`}
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
          help={
            venv?.isLocal
              ? '设置标识后，其他项目可以通过此标识引用该虚拟环境'
              : '环境的别名或标识'
          }
        >
          <Input placeholder="输入标识，留空则清除标识" allowClear />
        </Form.Item>
        {!venv?.isLocal && (
          <Form.Item label="描述" name="description" help="环境描述信息">
            <Input.TextArea placeholder="输入环境描述" rows={3} allowClear />
          </Form.Item>
        )}
        {venv && (
          <div style={{ marginTop: 16 }}>
            <Text type="secondary">当前环境：</Text>
            <div style={{ marginTop: 8 }}>
              <Space>
                <Tag color={venv.isLocal ? 'geekblue' : 'cyan'}>
                  {venv.isLocal ? '本地' : venv.nodeName}
                </Tag>
                <Text>Python {venv.version}</Text>
                {venv.current_project_id && (
                  <Tag color="blue">项目 ID: {venv.current_project_id}</Tag>
                )}
              </Space>
            </div>
          </div>
        )}
      </Form>
    </Modal>
  )
}

export default EditVenvKeyModal
