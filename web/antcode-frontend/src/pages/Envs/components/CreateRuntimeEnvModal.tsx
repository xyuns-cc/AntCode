/**
 * P4: 运行时环境创建入口。
 *
 * 后端 project_service 已经要求：项目创建时选 shared 环境**必须**从环境管理
 * 页里挑现成的（`共享环境必须从环境管理中选择现有环境`）。之前环境管理页只
 * 有编辑/删除/装包，没有创建入口，共享池实际无法建立。这里补上创建对话框。
 */
import type React from 'react'
import { useEffect, useMemo, useState } from 'react'
import { Modal, Form, Input, Select, App, Space, Alert } from 'antd'
import type { Worker } from '@/types'
import { runtimeService } from '@/services/runtimes'
import type { RuntimeScope } from '@/services/runtimes'

interface CreateRuntimeEnvModalProps {
  open: boolean
  workers: Worker[]
  defaultWorkerId?: string
  onClose: () => void
  onSuccess: () => void
}

const CreateRuntimeEnvModal: React.FC<CreateRuntimeEnvModalProps> = ({
  open,
  workers,
  defaultWorkerId,
  onClose,
  onSuccess,
}) => {
  const { message } = App.useApp()
  const [form] = Form.useForm()
  const [loading, setLoading] = useState(false)

  const onlineWorkers = useMemo(
    () => workers.filter((worker) => worker.status === 'online'),
    [workers]
  )

  useEffect(() => {
    if (open) {
      form.setFieldsValue({
        workerId: defaultWorkerId || onlineWorkers[0]?.id,
        scope: 'private' as RuntimeScope,
        pythonVersion: '3.11',
        envName: '',
        packages: '',
      })
    }
  }, [open, form, defaultWorkerId, onlineWorkers])

  const handleSubmit = async () => {
    try {
      const values = await form.validateFields()
      setLoading(true)
      const packages: string[] = values.packages
        ? String(values.packages).split(/\s+|[,;]/).map((p: string) => p.trim()).filter(Boolean)
        : []

      await runtimeService.createEnv(values.workerId, {
        env_name: values.envName?.trim() || undefined,
        python_version: values.pythonVersion,
        scope: values.scope,
        packages,
      })
      message.success('运行时环境创建成功')
      form.resetFields()
      onSuccess()
    } catch (error: unknown) {
      const errMsg = error instanceof Error ? error.message : '创建失败'
      message.error(errMsg)
    } finally {
      setLoading(false)
    }
  }

  return (
    <Modal
      title="新建运行时环境"
      open={open}
      onCancel={onClose}
      onOk={handleSubmit}
      confirmLoading={loading}
      okText="创建"
      cancelText="取消"
      forceRender
      destroyOnHidden
    >
      <Alert
        type="info"
        showIcon
        message="共享环境可被多个项目复用"
        description="项目创建时若选择共享作用域，必须从这里创建的共享环境中挑选。名称留空时按 shared-py{ver} / private-{uuid}-py{ver} 自动生成。"
        style={{ marginBottom: 16 }}
      />
      <Form form={form} layout="vertical" autoComplete="off">
        <Form.Item
          label="Worker"
          name="workerId"
          rules={[{ required: true, message: '必须选择 Worker' }]}
        >
          <Select
            placeholder="选择 Worker"
            options={onlineWorkers.map((w) => ({ value: w.id, label: w.name }))}
          />
        </Form.Item>
        <Form.Item
          label="作用域"
          name="scope"
          rules={[{ required: true }]}
        >
          <Select
            options={[
              { value: 'private', label: '私有 - 仅当前项目使用' },
              { value: 'shared', label: '共享 - 可被多个项目复用' },
            ]}
          />
        </Form.Item>
        <Form.Item
          label="Python 版本"
          name="pythonVersion"
          rules={[{ required: true, message: '必须指定 Python 版本' }]}
          help="例如 3.11.9；建议与 Worker 已装版本一致"
        >
          <Input placeholder="3.11.9" />
        </Form.Item>
        <Form.Item
          label="环境名称（可选）"
          name="envName"
          help="共享作用域下必须以 shared- 开头；留空自动生成"
        >
          <Input placeholder="shared-py311 / project-alias-py311" />
        </Form.Item>
        <Form.Item
          label="初始依赖（可选）"
          name="packages"
          help="以空格、逗号或分号分隔，例如 requests lxml"
        >
          <Input.TextArea
            placeholder="requests>=2.32, lxml"
            rows={2}
          />
        </Form.Item>
      </Form>
      <Space />
    </Modal>
  )
}

export default CreateRuntimeEnvModal
