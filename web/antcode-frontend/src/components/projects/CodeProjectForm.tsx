import React, { useCallback, useEffect, useMemo, useState } from 'react'
import { Button, Card, Col, Form, Input, Row, Select, Space, Tag, Typography } from 'antd'
import { CodeOutlined, PlusOutlined } from '@ant-design/icons'
import { FileIcon } from '@/utils/fileIcons'
import type { ProjectCreateRequest } from '@/types'
import { repositoryService } from '@/services/repositories'
import type { GitRepository } from '@/types/repository'

const { Title, Text } = Typography
const { TextArea } = Input
const { Option } = Select

const LANGUAGE_OPTIONS = [
  { value: 'python', label: 'Python', extension: '.py', color: '#3776ab' },
  { value: 'javascript', label: 'JavaScript', extension: '.js', color: '#f7df1e' },
  { value: 'typescript', label: 'TypeScript', extension: '.ts', color: '#3178c6' },
  { value: 'java', label: 'Java', extension: '.java', color: '#b07219' },
  { value: 'go', label: 'Go', extension: '.go', color: '#00add8' }
]

interface CodeProjectFormInitialData extends Omit<Partial<ProjectCreateRequest>, 'tags'> {
  tags?: string | string[]
}

interface CodeProjectFormProps {
  initialData?: CodeProjectFormInitialData
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

const CodeProjectForm: React.FC<CodeProjectFormProps> = ({
  initialData = {},
  onDataChange,
  onSubmit,
  onValidationChange,
  onRef
}) => {
  const [form] = Form.useForm<ProjectCreateRequest>()
  const [dependencies, setDependencies] = useState<string[]>(initialData.dependencies || [])
  const [newDependency, setNewDependency] = useState('')
  const [repositories, setRepositories] = useState<GitRepository[]>([])
  const repositoryIdValue = Form.useWatch('repository_id', form)
  const subdirValue = Form.useWatch('subdir', form)
  const entryPointValue = Form.useWatch('code_entry_point', form)

  const isValid = useMemo(() => {
    if (!repositoryIdValue || String(repositoryIdValue).trim() === '') {
      return { valid: false, tooltip: 'Git 来源必须选择代码仓库' }
    }
    if (!subdirValue || String(subdirValue).trim() === '') {
      return { valid: false, tooltip: 'Git 代码项目必须填写仓库子目录' }
    }
    if (!entryPointValue || String(entryPointValue).trim() === '') {
      return { valid: false, tooltip: 'Git 代码项目必须填写入口文件' }
    }
    return { valid: true, tooltip: '' }
  }, [entryPointValue, repositoryIdValue, subdirValue])

  useEffect(() => {
    onValidationChange?.(isValid.valid, isValid.tooltip)
  }, [isValid, onValidationChange])

  useEffect(() => {
    repositoryService.list().then(setRepositories)
  }, [])

  useEffect(() => {
    onRef?.({ submit: () => form.submit() })
  }, [form, onRef])

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
      type: 'code',
      include_paths: normalizeList(values.include_paths),
      dependencies,
      tags: normalizeTags(values.tags)
    })
  }

  const initialValues = {
    ...initialData,
    language: initialData.language || 'python'
  }

  return (
    <div>
      <div style={{ textAlign: 'center', marginBottom: 24 }}>
        <Title level={4}>
          <CodeOutlined style={{ marginRight: 8, color: '#722ed1' }} />
          代码项目配置
        </Title>
        <Text type="secondary">通过 Git 仓库提供代码来源</Text>
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
              <Form.Item name="language" label="编程语言" rules={[{ required: true, message: '请选择编程语言' }]}>
                <Select showSearch placeholder="选择编程语言" optionFilterProp="children">
                  {LANGUAGE_OPTIONS.map((option) => (
                    <Option key={option.value} value={option.value}>
                      <Space>
                        <FileIcon extension={option.extension} size={16} />
                        <span style={{ color: option.color }}>{option.label}</span>
                      </Space>
                    </Option>
                  ))}
                </Select>
              </Form.Item>
            </Col>
          </Row>

          <Form.Item name="tags" label="项目标签" tooltip="多个标签用逗号分隔">
            <Input placeholder="例如: 工具,脚本,自动化" />
          </Form.Item>

          <Form.Item name="description" label="项目描述">
            <TextArea rows={3} placeholder="请描述代码的功能和用途" maxLength={500} showCount />
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

          <Form.Item
            name="code_entry_point"
            label="入口文件"
            tooltip="指定项目子目录内的入口脚本，如 main.py"
            rules={[{ required: true, message: '请输入入口文件' }]}
          >
            <Input placeholder="例如: main.py" />
          </Form.Item>
        </Card>

        <Card title="依赖与文档" size="small">
          <Space.Compact style={{ width: '100%', marginBottom: 16 }}>
            <Input
              placeholder="输入依赖包名"
              value={newDependency}
              onChange={(event) => setNewDependency(event.target.value)}
              onPressEnter={handleAddDependency}
            />
            <Button type="primary" icon={<PlusOutlined />} onClick={handleAddDependency} disabled={!newDependency.trim()}>
              添加
            </Button>
          </Space.Compact>

          {dependencies.map((dependency) => (
            <Tag key={dependency} closable onClose={() => handleRemoveDependency(dependency)} style={{ marginBottom: 8 }}>
              {dependency}
            </Tag>
          ))}

          <Form.Item name="documentation" label="代码文档" style={{ marginTop: 12 }}>
            <TextArea rows={8} placeholder="请添加代码的使用说明、API文档等..." maxLength={2000} showCount />
          </Form.Item>
        </Card>
      </Form>
    </div>
  )
}

export default React.memo(CodeProjectForm)
