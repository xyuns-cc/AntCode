import type React from 'react'
import { useEffect, useState } from 'react'
import { Button, Drawer, Form, Input, Select, Space, Typography } from 'antd'
import { gitCredentialService } from '@/services/gitCredentials'
import type { GitCredential } from '@/types'
import type { GitRepository } from '@/types/repository'

interface Props {
  open: boolean
  // null = 新增；非 null = 编辑该仓库
  editing: GitRepository | null
  form: ReturnType<typeof Form.useForm>[0]
  onClose: () => void
  onSubmit: () => void
}

const { Text } = Typography

const DEFAULT_REF = 'main'

const blankValues = {
  name: '',
  url: '',
  default_ref: DEFAULT_REF,
  credential_id: undefined,
}

const useCredentials = (open: boolean) => {
  const [credentials, setCredentials] = useState<GitCredential[]>([])
  useEffect(() => {
    if (!open) return
    gitCredentialService.listGitCredentials().then(setCredentials)
  }, [open])
  return credentials
}

const CredentialOptions = ({ credentials }: { credentials: GitCredential[] }) => (
  <>
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
  </>
)

const RepositoryFormDrawer: React.FC<Props> = ({ open, editing, form, onClose, onSubmit }) => {
  const credentials = useCredentials(open)

  // Drawer 带 destroyOnClose，Form 每次打开都是新挂载的，必须在打开时回填；
  // 靠 initialValues 只在首次挂载生效，第二次点「编辑」会显示上一条的数据。
  useEffect(() => {
    if (!open) return
    form.setFieldsValue(
      editing
        ? {
            name: editing.name,
            url: editing.url,
            default_ref: editing.default_ref,
            credential_id: editing.credential_id ?? undefined,
          }
        : blankValues
    )
  }, [open, editing, form])

  return (
    <Drawer
      title={editing ? '编辑仓库' : '新增仓库'}
      open={open}
      width={520}
      onClose={onClose}
      destroyOnClose
    >
      <Form layout="vertical" form={form}>
        <Form.Item name="name" label="名称" rules={[{ required: true }]}>
          <Input />
        </Form.Item>
        <Form.Item name="url" label="Git URL" rules={[{ required: true }]}>
          <Input />
        </Form.Item>
        <Form.Item
          name="default_ref"
          label="默认引用"
          rules={[{ required: true }]}
          extra="每次扫描/派发默认使用的分支或 tag。只想临时看另一个分支时，用扫描抽屉里的「本次扫描引用」，不必改这里。"
        >
          <Input />
        </Form.Item>
        <Form.Item name="credential_id" label="Git 凭证">
          <Select allowClear placeholder="公共仓库可不选择" optionLabelProp="label">
            <CredentialOptions credentials={credentials} />
          </Select>
        </Form.Item>
        <Button type="primary" onClick={onSubmit}>
          保存
        </Button>
      </Form>
    </Drawer>
  )
}

export default RepositoryFormDrawer
