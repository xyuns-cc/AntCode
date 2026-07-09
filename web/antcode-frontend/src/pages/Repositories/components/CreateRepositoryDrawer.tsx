import type React from 'react'
import { useEffect, useState } from 'react'
import { Button, Drawer, Form, Input, Select, Space, Typography } from 'antd'
import { gitCredentialService } from '@/services/gitCredentials'
import type { GitCredential } from '@/types'

interface Props {
  open: boolean
  form: ReturnType<typeof Form.useForm>[0]
  onClose: () => void
  onSubmit: () => void
}

const { Text } = Typography

const CreateRepositoryDrawer: React.FC<Props> = ({ open, form, onClose, onSubmit }) => {
  const [credentials, setCredentials] = useState<GitCredential[]>([])

  useEffect(() => {
    if (!open) return
    gitCredentialService.listGitCredentials().then(setCredentials)
  }, [open])

  return (
    <Drawer title="新增仓库" open={open} width={520} onClose={onClose} destroyOnClose>
      <Form layout="vertical" form={form} initialValues={{ default_ref: 'main' }}>
        <Form.Item name="name" label="名称" rules={[{ required: true }]}>
          <Input />
        </Form.Item>
        <Form.Item name="url" label="Git URL" rules={[{ required: true }]}>
          <Input />
        </Form.Item>
        <Form.Item name="default_ref" label="默认引用" rules={[{ required: true }]}>
          <Input />
        </Form.Item>
        <Form.Item name="credential_id" label="Git 凭证">
          <Select
            allowClear
            placeholder="公共仓库可不选择"
            optionLabelProp="label"
          >
            {credentials.map((credential) => (
              <Select.Option key={credential.id} value={credential.id} label={credential.name}>
                <Space direction="vertical" size={0}>
                  <span>{credential.name}</span>
                  <Text type="secondary" style={{ fontSize: 12 }}>
                    {credential.host_scope}
                  </Text>
                </Space>
              </Select.Option>
            ))}
          </Select>
        </Form.Item>
        <Button type="primary" onClick={onSubmit}>保存</Button>
      </Form>
    </Drawer>
  )
}

export default CreateRepositoryDrawer
