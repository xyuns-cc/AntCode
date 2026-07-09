import React, { useCallback, useEffect, useMemo, useState } from 'react'
import { Button, Card, Col, Form, Input, Row, Select, Space, Tag, Typography } from 'antd'
import { FileOutlined, PlusOutlined } from '@ant-design/icons'
import type { ProjectCreateRequest } from '@/types'
import { repositoryService } from '@/services/repositories'
import type { GitRepository } from '@/types/repository'

const { Title, Text } = Typography
const { TextArea } = Input
const { Option } = Select

interface FileProjectFormInitialData extends Omit<Partial<ProjectCreateRequest>, 'tags'> {
  tags?: string | string[]
  file_info?: {
    entry_point?: string
    repository_id?: string
    ref?: string
    subdir?: string
    include_paths?: string[]
  }
}

interface FileProjectFormProps {
  initialData?: FileProjectFormInitialData
  onDataChange?: (data: Partial<ProjectCreateRequest>) => void
  onSubmit: (data: Record<string, unknown>) => void
  loading?: boolean
  isEdit?: boolean
  onValidationChange?: (isValid: boolean, tooltip: string) => void
  onRef?: (ref: { submit: () => void }) => void
}

const normalizeTags = (tags?: string | string[]): string[] => {
  if (Array.isArray(tags)) return tags
  return (tags || '').split(',').map((tag) => tag.trim()).filter(Boolean)
}

const normalizeList = (value?: string[] | string): string[] => {
  if (Array.isArray(value)) return value
  return (value || '').split(',').map((item) => item.trim()).filter(Boolean)
}

const FileProjectForm: React.FC<FileProjectFormProps> = ({
  initialData = {},
  onDataChange,
  onSubmit,
  isEdit = false,
  onValidationChange,
  onRef
}) => {
  const [form] = Form.useForm<ProjectCreateRequest>()
  const [dependencies, setDependencies] = useState<string[]>(initialData.dependencies || [])
  const [newDependency, setNewDependency] = useState('')
  const [repositories, setRepositories] = useState<GitRepository[]>([])
  const repositoryIdValue = Form.useWatch('repository_id', form)
  const subdirValue = Form.useWatch('subdir', form)
  const entryPointValue = Form.useWatch('entry_point', form)

  const isValid = useMemo(() => {
    if (!repositoryIdValue || String(repositoryIdValue).trim() === '') {
      return { valid: false, tooltip: 'Git 来源必须选择代码仓库' }
    }
    if (!subdirValue || String(subdirValue).trim() === '') {
      return { valid: false, tooltip: 'Git 文件项目必须填写仓库子目录' }
    }
    if (!entryPointValue || String(entryPointValue).trim() === '') {
      return { valid: false, tooltip: 'Git 文件项目必须填写入口文件' }
    }
    return { valid: true, tooltip: '' }
  }, [entryPointValue, repositoryIdValue, subdirValue])

  useEffect(() => {
    onValidationChange?.(isValid.valid, isValid.tooltip)
  }, [isValid, onValidationChange])

  useEffect(() => {
    onRef?.({ submit: () => form.submit() })
  }, [form, onRef])

  useEffect(() => {
    repositoryService.list().then(setRepositories)
  }, [])

  const emitDataChange = useCallback((values: ProjectCreateRequest, deps = dependencies) => {
    onDataChange?.({
      ...values,
      dependencies: deps
    })
  }, [dependencies, onDataChange])

  const handleAddDependency = () => {
    const dependency = newDependency.trim()
    if (!dependency || dependencies.includes(dependency)) return
    const nextDependencies = [...dependencies, dependency]
    setDependencies(nextDependencies)
    setNewDependency('')
    emitDataChange(form.getFieldsValue(), nextDependencies)
  }

  const handleRemoveDependency = (dependency: string) => {
    const nextDependencies = dependencies.filter((item) => item !== dependency)
    setDependencies(nextDependencies)
    emitDataChange(form.getFieldsValue(), nextDependencies)
  }

  const handleFinish = (values: ProjectCreateRequest) => {
    onSubmit({
      ...values,
      type: 'file',
      include_paths: normalizeList(values.include_paths),
      dependencies,
      tags: normalizeTags(values.tags)
    })
  }

  const initialValues = {
    ...initialData
  }

  return (
    <div>
      <div style={{ textAlign: 'center', marginBottom: 24 }}>
        <Title level={4}>
          <FileOutlined style={{ marginRight: 8, color: '#1890ff' }} />
          {isEdit ? '编辑文件项目' : '文件项目配置'}
        </Title>
        <Text type="secondary">通过 Git 仓库提供文件项目来源</Text>
      </div>

      <Form
        form={form}
        layout="vertical"
        initialValues={initialValues}
        onFinish={handleFinish}
        onValuesChange={(_, allValues) => emitDataChange(allValues)}
      >
        <Card title="基本信息" size="small" style={{ marginBottom: 16 }}>
          <Row gutter={16}>
            <Col span={12}>
              <Form.Item name="name" label="项目名称" rules={[{ required: true, message: '请输入项目名称' }]}>
                <Input placeholder="请输入项目名称" />
              </Form.Item>
            </Col>
            <Col span={12}>
              <Form.Item name="tags" label="项目标签" tooltip="多个标签用逗号分隔">
                <Input placeholder="例如: 爬虫,数据处理,自动化" />
              </Form.Item>
            </Col>
          </Row>

          <Form.Item name="description" label="项目描述">
            <TextArea rows={3} placeholder="请描述项目的功能和用途" maxLength={500} showCount />
          </Form.Item>
        </Card>

        <Card title="Git 来源" size="small" style={{ marginBottom: 16 }}>
          <Form.Item name="repository_id" label="代码仓库" rules={[{ required: true, message: '请选择代码仓库' }]}>
            <Select showSearch placeholder="选择已管理仓库" optionFilterProp="label">
              {repositories.map((repository) => (
                <Option key={repository.id} value={repository.id} label={repository.name}>
                  <Space direction="vertical" size={0}>
                    <span>{repository.name}</span>
                    <Text type="secondary" style={{ fontSize: 12 }}>{repository.url}</Text>
                  </Space>
                </Option>
              ))}
            </Select>
          </Form.Item>

          <Row gutter={16}>
            <Col span={12}>
              <Form.Item name="ref" label="Git 引用">
                <Input placeholder="main" />
              </Form.Item>
            </Col>
            <Col span={12}>
              <Form.Item name="subdir" label="项目子目录" rules={[{ required: true, message: '请输入仓库内项目子目录' }]}>
                <Input placeholder="spiders/news" />
              </Form.Item>
            </Col>
          </Row>

          <Form.Item name="include_paths" label="共享目录">
            <Input placeholder="libs/common, libs/utils" />
          </Form.Item>
        </Card>

        <Card title="运行配置" size="small" style={{ marginBottom: 16 }}>
          <Row gutter={16}>
            <Col span={8}>
              <Form.Item
                name="language"
                label="执行语言"
                tooltip="选择项目的执行语言；worker 会自动装配对应依赖并选运行时"
                initialValue="python"
              >
                <Select>
                  <Option value="python">Python</Option>
                  <Option value="javascript">JavaScript (Node.js)</Option>
                  <Option value="typescript">TypeScript</Option>
                  <Option value="java">Java</Option>
                  <Option value="go">Go</Option>
                </Select>
              </Form.Item>
            </Col>
            <Col span={16}>
              <Form.Item
                name="entry_point"
                label="入口文件"
                tooltip="指定项目的主入口文件；后缀决定运行时（.py/.js/.ts/.jar/.go）"
                rules={[{ required: true, message: '请输入入口文件' }]}
              >
                <Input placeholder="例如: main.py / index.js / app.jar / main.go" />
              </Form.Item>
            </Col>
          </Row>

          <Row gutter={16}>
            <Col span={12}>
              <Form.Item name="runtime_config" label="运行时配置" tooltip='JSON格式，如 {"max_workers": 4}'>
                <TextArea rows={3} placeholder='{"max_workers": 4, "timeout": 30}' />
              </Form.Item>
            </Col>
            <Col span={12}>
              <Form.Item name="environment_vars" label="环境变量" tooltip='JSON格式，如 {"API_KEY": "your_key"}'>
                <TextArea rows={3} placeholder='{"API_KEY": "your_key", "DEBUG": "true"}' />
              </Form.Item>
            </Col>
          </Row>

          <Form.Item label="Python 依赖包">
            <Space direction="vertical" style={{ width: '100%' }}>
              <Space.Compact style={{ width: '100%' }}>
                <Input
                  placeholder="输入依赖包名，如 requests"
                  value={newDependency}
                  onChange={(event) => setNewDependency(event.target.value)}
                  onPressEnter={handleAddDependency}
                />
                <Button type="primary" icon={<PlusOutlined />} onClick={handleAddDependency} disabled={!newDependency.trim()}>
                  添加
                </Button>
              </Space.Compact>

              {dependencies.map((dependency) => (
                <Tag key={dependency} closable onClose={() => handleRemoveDependency(dependency)} style={{ marginBottom: 4 }}>
                  {dependency}
                </Tag>
              ))}
            </Space>
          </Form.Item>
        </Card>
      </Form>
    </div>
  )
}

export default React.memo(FileProjectForm)
