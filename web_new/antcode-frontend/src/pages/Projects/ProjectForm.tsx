import React, { useEffect, useState } from 'react'
import { Card, Form, Input, Select, Button, Space, Tabs, Spin, Alert, Row, Col, theme, Divider } from 'antd'
import { ArrowLeftOutlined, SaveOutlined, FileTextOutlined, ToolOutlined, CodeOutlined } from '@ant-design/icons'
import { useParams, useNavigate } from 'react-router-dom'
import { projectService } from '@/services/projects'
import { workerService } from '@/services/workers'
import { runtimeService } from '@/services/runtimes'
import { validationRules } from '@/utils/validators'
import { RuleProjectForm, CodeProjectForm, FileProjectForm, RegionWorkerSelector } from '@/components/projects'
import { useAuth } from '@/hooks/useAuth'
import { runtimeScopeOptions } from '@/config/displayConfig'
import type { Project, ProjectCreateRequest, ProjectUpdateRequest, Worker, RuntimeEnv } from '@/types'

const { TextArea } = Input

// 项目类型配置
const PROJECT_TYPES = [
  {
    value: 'file',
    label: '文件项目',
    icon: <FileTextOutlined />,
    description: '上传文件进行数据提取'
  },
  {
    value: 'rule',
    label: '规则项目',
    icon: <ToolOutlined />,
    description: '配置规则进行网页抓取'
  },
  {
    value: 'code',
    label: '代码项目',
    icon: <CodeOutlined />,
    description: '编写代码自定义采集逻辑'
  }
]

// 项目类型选择器组件
const ProjectTypeSelector: React.FC<{
  value?: string
  onChange?: (value: string) => void
}> = ({ value, onChange }) => {
  const { token } = theme.useToken()
  
  return (
    <Row gutter={[12, 12]}>
      {PROJECT_TYPES.map((type) => (
        <Col key={type.value} xs={24} sm={12}>
          <div
            onClick={() => onChange?.(type.value)}
            style={{
              padding: '12px 16px',
              borderRadius: 8,
              border: `1px solid ${value === type.value ? token.colorPrimary : token.colorBorder}`,
              background: value === type.value ? token.colorPrimaryBg : token.colorBgContainer,
              cursor: 'pointer',
              transition: 'all 0.2s',
              display: 'flex',
              alignItems: 'center',
              gap: 12
            }}
          >
            <div style={{
              fontSize: 20,
              color: value === type.value ? token.colorPrimary : token.colorTextSecondary
            }}>
              {type.icon}
            </div>
            <div style={{ flex: 1 }}>
              <div style={{
                fontWeight: 500,
                color: value === type.value ? token.colorPrimary : token.colorText
              }}>
                {type.label}
              </div>
              <div style={{
                fontSize: 12,
                color: token.colorTextSecondary,
                marginTop: 2
              }}>
                {type.description}
              </div>
            </div>
          </div>
        </Col>
      ))}
    </Row>
  )
}

const ProjectForm: React.FC = () => {
  const { id } = useParams<{ id: string }>()
  const navigate = useNavigate()
  const [form] = Form.useForm()
  const [loading, setLoading] = useState(false)
  const [runtimeScope, setRuntimeScope] = useState<string>('private')
  const [dependencies, setDependencies] = useState<string[]>([])
  const [pythonVersion, setPythonVersion] = useState<string>('')
  const [existingEnvName, setExistingEnvName] = useState<string>('')
  const [sharedEnvs, setSharedEnvs] = useState<RuntimeEnv[]>([])
  const [envLoading, setEnvLoading] = useState(false)
  const [workers, setWorkers] = useState<Worker[]>([])
  const [fetchLoading, setFetchLoading] = useState(false)
  const [project, setProject] = useState<Project | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [activeTab, setActiveTab] = useState('basic')
  
  // 区域配置
  const [regionConfig, setRegionConfig] = useState<{
    region?: string
    require_render?: boolean
  }>({})
  
  // 编辑模式下的表单引用
  const [ruleFormRef, setRuleFormRef] = useState<{ submit: () => void } | null>(null)
  const [codeFormRef, setCodeFormRef] = useState<{ submit: () => void } | null>(null)
  const [fileFormRef, setFileFormRef] = useState<{ submit: () => void } | null>(null)
  const { isAuthenticated, loading: authLoading } = useAuth()

  const isEdit = !!id
  const onlineWorkers = workers.filter((worker) => worker.status === 'online')
  const selectedWorkerId = Form.useWatch('worker_id', form)

  useEffect(() => {
    if (isEdit && id && isAuthenticated && !authLoading) {
      const fetchProject = async () => {
        setFetchLoading(true)
        setError(null)
        try {
          const projectData = await projectService.getProject(id)
          setProject(projectData)
          form.setFieldsValue({
            name: projectData.name,
            type: projectData.type,
            description: projectData.description,
            tags: Array.isArray(projectData.tags) ? projectData.tags.join(', ') : projectData.tags
          })
          // 加载区域配置
          if (projectData.region) {
            setRegionConfig({
              region: projectData.region,
              require_render: projectData.rule_info?.engine === 'browser'
            })
          }
        } catch (error: unknown) {
          const axiosError = error as { response?: { data?: { message?: string } }; message?: string }
          const errorMessage = axiosError.response?.data?.message || axiosError.message || '获取项目信息失败'
          setError(errorMessage)
        } finally {
          setFetchLoading(false)
        }
      }
      fetchProject()
    }
    if (!isEdit && isAuthenticated && !authLoading) {
      workerService
        .getAllWorkers()
        .then((items) => setWorkers(items))
        .catch(() => setWorkers([]))
    }
  }, [id, isEdit, form, isAuthenticated, authLoading])

  useEffect(() => {
    if (!selectedWorkerId || runtimeScope !== 'shared') {
      setSharedEnvs([])
      return
    }

    setEnvLoading(true)
    runtimeService
      .listEnvs(selectedWorkerId, 'shared')
      .then((items) => setSharedEnvs(items))
      .catch(() => setSharedEnvs([]))
      .finally(() => setEnvLoading(false))
  }, [selectedWorkerId, runtimeScope])

  useEffect(() => {
    if (existingEnvName && !sharedEnvs.find((env) => env.name === existingEnvName)) {
      setExistingEnvName('')
    }
  }, [existingEnvName, sharedEnvs])

  const handleSubmit = async (values: { name: string; type?: string; description?: string; tags?: string; worker_id?: string }) => {
    setLoading(true)
    try {
      if (isEdit && project) {
        const updateData: ProjectUpdateRequest = {
          name: values.name,
          description: values.description,
          tags: values.tags,
          // 区域配置
          region: regionConfig.region,
        }
        await projectService.updateProject(project.id, updateData)
        // 成功提示由拦截器统一处理
      } else {
        const createData: ProjectCreateRequest = {
          name: values.name,
          type: values.type as ProjectCreateRequest['type'],
          description: values.description,
          tags: values.tags ? values.tags.split(',').map((t: string) => t.trim()).filter(Boolean) : undefined,
          worker_id: values.worker_id,
          runtime_scope: runtimeScope,
          use_existing_env: runtimeScope === 'shared',
          existing_env_name: runtimeScope === 'shared' ? existingEnvName || undefined : undefined,
          python_version: runtimeScope === 'private' ? pythonVersion : undefined,
          dependencies,
          // 区域配置
          region: regionConfig.region,
        }
        await projectService.createProject(createData)
        // 成功提示由拦截器统一处理
      }
      navigate('/projects')
    } catch {
      // 错误提示由拦截器统一处理
    } finally {
      setLoading(false)
    }
  }

  const handleRuleSubmit = async (ruleData: Record<string, unknown>) => {
    setLoading(true)
    try {
      if (isEdit && project) {
        await projectService.updateRuleConfig(project.id, ruleData)
        // 成功提示由拦截器统一处理
        navigate('/projects')
      }
    } catch {
      // 错误提示由拦截器统一处理
    } finally {
      setLoading(false)
    }
  }

  const handleCodeSubmit = async (codeData: Record<string, unknown>) => {
    setLoading(true)
    try {
      if (isEdit && project) {
        await projectService.updateCodeConfig(project.id, codeData)
        // 成功提示由拦截器统一处理
        navigate('/projects')
      }
    } catch {
      // 错误提示由拦截器统一处理
    } finally {
      setLoading(false)
    }
  }

  const handleFileSubmit = async (fileData: { entry_point?: string; runtime_config?: Record<string, unknown>; environment_vars?: Record<string, unknown>; file?: File }) => {
    setLoading(true)
    try {
      if (isEdit && project) {
        // 构建更新数据
        const updateData = {
          entry_point: fileData.entry_point,
          runtime_config: fileData.runtime_config,
          environment_vars: fileData.environment_vars,
          file: fileData.file  // 可能为undefined，表示不替换文件
        }

        await projectService.updateFileConfig(project.id, updateData)

        // 成功提示由拦截器统一处理
        navigate('/projects')
      }
    } catch {
      // 错误提示由拦截器统一处理
    } finally {
      setLoading(false)
    }
  }

  // 渲染基本信息编辑表单
  const renderBasicForm = () => (
    <Form
      form={form}
      layout="vertical"
      onFinish={handleSubmit}
      style={{ maxWidth: 600 }}
    >
      <Form.Item
        name="name"
        label="项目名称"
        rules={validationRules.projectName}
      >
        <Input placeholder="请输入项目名称" />
      </Form.Item>

      {!isEdit && (
        <Form.Item
          name="type"
          label="项目类型"
          rules={[{ required: true, message: '请选择项目类型' }]}
        >
          <ProjectTypeSelector />
        </Form.Item>
      )}

      <Form.Item
        name="description"
        label="项目描述"
      >
        <TextArea
          rows={4}
          placeholder="请输入项目描述"
        />
      </Form.Item>

      {!isEdit && (
        <>
          <Form.Item
            name="worker_id"
            label="Worker"
            rules={[{ required: true, message: '请选择 Worker' }]}
          >
            <Select
              placeholder={onlineWorkers.length > 0 ? '选择在线 Worker' : '暂无在线 Worker'}
              options={onlineWorkers.map((worker) => ({
                value: worker.id,
                label: `${worker.name} (${worker.host}:${worker.port})`,
              }))}
            />
          </Form.Item>

          {onlineWorkers.length === 0 && (
            <Alert
              message="暂无在线 Worker"
              description="请先确保至少有一个 Worker 在线，以便创建运行环境"
              type="warning"
              showIcon
              style={{ marginBottom: 16 }}
            />
          )}

          <Form.Item
            name="runtime_scope"
            label="虚拟环境作用域"
            rules={[{ required: true, message: '请选择虚拟环境作用域' }]}
          >
            <Select
              placeholder="请选择环境作用域"
              value={runtimeScope}
              onChange={(v) => {
                setRuntimeScope(v)
                setExistingEnvName('')
                if (v === 'shared') {
                  setPythonVersion('')
                }
              }}
              options={runtimeScopeOptions}
            />
          </Form.Item>

          {runtimeScope === 'shared' && (
            <>
              <Form.Item
                name="existing_env_name"
                label="共享环境"
                rules={[{ required: true, message: '请选择共享环境' }]}
              >
                <Select
                  placeholder="选择共享环境"
                  value={existingEnvName}
                  onChange={setExistingEnvName}
                  loading={envLoading}
                  options={sharedEnvs.map((env) => ({
                    value: env.name,
                    label: `${env.name} (Python ${env.python_version})`
                  }))}
                />
              </Form.Item>
              {sharedEnvs.length === 0 && (
                <Alert
                  message="暂无共享环境"
                  description="请先在运行时管理中创建共享环境"
                  type="warning"
                  showIcon
                  style={{ marginBottom: 16 }}
                />
              )}
            </>
          )}

          {runtimeScope === 'private' && (
            <Form.Item
              name="python_version"
              label="Python 版本"
              rules={[{ required: true, message: '请输入 Python 版本' }]}
            >
              <Input
                placeholder="例如 3.11.9"
                value={pythonVersion}
                onChange={(e) => setPythonVersion(e.target.value)}
              />
            </Form.Item>
          )}

          <Form.Item name="dependencies" label="依赖">
            <Select
              mode="tags"
              placeholder="输入包名后回车添加，例如: requests==2.32.3"
              onChange={(vals) => setDependencies(vals as string[])}
              tokenSeparators={[',']}
            />
          </Form.Item>
        </>
      )}

      <Form.Item
        name="tags"
        label="标签"
      >
        <Input placeholder="请输入标签，多个标签用逗号分隔" />
      </Form.Item>

      <Divider orientation="left">执行区域配置</Divider>
      
      <RegionWorkerSelector
        value={regionConfig}
        onChange={setRegionConfig}
        requireRender={regionConfig.require_render}
      />

      <Form.Item style={{ marginTop: 24 }}>
        <Space>
          <Button
            type="primary"
            htmlType="submit"
            loading={loading}
            icon={<SaveOutlined />}
          >
            {isEdit ? '更新基本信息' : '创建'}
          </Button>
          <Button onClick={() => navigate('/projects')}>
            取消
          </Button>
        </Space>
      </Form.Item>
    </Form>
  )

  // 渲染规则项目编辑表单
  const renderRuleForm = () => {
    if (!project?.rule_info) {
      return null
    }

    const initialRuleData = {
      name: project.name,
      description: project.description,
      tags: Array.isArray(project.tags) ? project.tags.join(', ') : project.tags,
      engine: project.rule_info.engine,
      target_url: project.rule_info.target_url,
      url_pattern: project.rule_info.url_pattern,
      request_delay: project.rule_info.request_delay / 1000, // 转换为秒
      request_method: project.rule_info.request_method,
      callback_type: project.rule_info.callback_type,
      max_pages: project.rule_info.max_pages,
      start_page: project.rule_info.start_page,
      priority: project.rule_info.priority,
      retry_count: project.rule_info.retry_count,
      timeout: project.rule_info.timeout,
      dont_filter: project.rule_info.dont_filter,
      // v2.0.0 统一字段
      extraction_rules: (() => {
        if (project.rule_info.extraction_rules) {
          if (Array.isArray(project.rule_info.extraction_rules)) {
            return JSON.stringify(project.rule_info.extraction_rules)
          }
          return project.rule_info.extraction_rules
        }
        return '[]'
      })(),
      pagination_config: (() => {
        if (project.rule_info.pagination_config) {
          if (typeof project.rule_info.pagination_config === 'object') {
            return JSON.stringify(project.rule_info.pagination_config)
          }
          return project.rule_info.pagination_config
        }
        return '{}'
      })(),
      headers: (() => {
        const headers = project.rule_info.headers
        if (!headers) return ''
        if (typeof headers === 'string') return headers
        return JSON.stringify(headers, null, 2)
      })(),
      cookies: (() => {
        const cookies = project.rule_info.cookies
        if (!cookies) return ''
        if (typeof cookies === 'string') return cookies
        return JSON.stringify(cookies, null, 2)
      })(),
      // v2.0.0 新增字段
      proxy_config: project.rule_info.proxy_config,
      anti_spider: project.rule_info.anti_spider,
      task_config: project.rule_info.task_config,
      data_schema: project.rule_info.data_schema
    }

    return (
      <RuleProjectForm
        initialData={initialRuleData}
        onSubmit={handleRuleSubmit}
        loading={loading}
        isEdit={true}
        onRef={setRuleFormRef}
      />
    )
  }

  // 渲染代码项目编辑表单
  const renderCodeForm = () => {
    if (!project?.code_info) return null

    const initialCodeData = {
      name: project.name,
      description: project.description,
      tags: Array.isArray(project.tags) ? project.tags.join(', ') : project.tags,
      language: project.code_info.language,
      version: project.code_info.version,
      entry_point: project.code_info.entry_point,
      documentation: project.code_info.documentation,
      code_content: project.code_info.content,
      dependencies: project.dependencies || []
    }

    return (
      <CodeProjectForm
        initialData={initialCodeData}
        onSubmit={handleCodeSubmit}
        loading={loading}
        isEdit={true}
        onRef={setCodeFormRef}
      />
    )
  }

  // 渲染文件项目编辑表单
  const renderFileForm = () => {
    if (!project?.file_info) return null

    const initialFileData = {
      name: project.name,
      description: project.description,
      tags: Array.isArray(project.tags) ? project.tags.join(', ') : project.tags,
      entry_point: project.file_info.entry_point,
      runtime_config: project.file_info.runtime_config ? JSON.stringify(project.file_info.runtime_config, null, 2) : '',
      environment_vars: project.file_info.environment_vars ? JSON.stringify(project.file_info.environment_vars, null, 2) : '',
      dependencies: project.dependencies || [],
      file_info: project.file_info
    }

    return (
      <FileProjectForm
        initialData={initialFileData}
        onSubmit={handleFileSubmit}
        loading={loading}
        isEdit={true}
        onRef={setFileFormRef}
      />
    )
  }

  // 认证检查
  if (authLoading) {
    return (
      <div style={{ textAlign: 'center', padding: '50px' }}>
        <Spin size="large" />
        <div style={{ marginTop: '16px' }}>正在验证登录状态...</div>
      </div>
    )
  }

  if (!isAuthenticated) {
    return (
      <div style={{ textAlign: 'center', padding: '50px' }}>
        <Alert
          message="需要登录"
          description="请先登录后再访问此页面"
          type="warning"
          showIcon
          action={
            <Button type="primary" onClick={() => navigate('/login')}>
              去登录
            </Button>
          }
        />
      </div>
    )
  }

  // 编辑模式下的加载状态
  if (isEdit && fetchLoading) {
    return (
      <Card
        title={
          <Space>
            <Button
              type="text"
              icon={<ArrowLeftOutlined />}
              onClick={() => navigate('/projects')}
            >
              返回
            </Button>
            <span>编辑项目</span>
          </Space>
        }
      >
        <div style={{ textAlign: 'center', padding: '50px' }}>
          <Spin size="large" />
          <div style={{ marginTop: '16px' }}>正在加载项目信息...</div>
        </div>
      </Card>
    )
  }

  // 编辑模式下的错误状态
  if (isEdit && error) {
    return (
      <Card
        title={
          <Space>
            <Button
              type="text"
              icon={<ArrowLeftOutlined />}
              onClick={() => navigate('/projects')}
            >
              返回
            </Button>
            <span>编辑项目</span>
          </Space>
        }
      >
        <Alert
          message="加载失败"
          description={error}
          type="error"
          showIcon
          action={
            <Space>
              <Button onClick={() => window.location.reload()}>
                重试
              </Button>
              <Button type="primary" onClick={() => navigate('/projects')}>
                返回项目列表
              </Button>
            </Space>
          }
        />
      </Card>
    )
  }

  // 编辑模式下项目不存在
  if (isEdit && !fetchLoading && !project) {
    return (
      <Card
        title={
          <Space>
            <Button
              type="text"
              icon={<ArrowLeftOutlined />}
              onClick={() => navigate('/projects')}
            >
              返回
            </Button>
            <span>编辑项目</span>
          </Space>
        }
      >
        <Alert
          message="项目不存在"
          description="您要编辑的项目不存在或已被删除"
          type="warning"
          showIcon
          action={
            <Button type="primary" onClick={() => navigate('/projects')}>
              返回项目列表
            </Button>
          }
        />
      </Card>
    )
  }

  // 获取当前活动tab的保存按钮
  const getSaveButton = () => {
    if (!isEdit) return null
    
    const handleSave = () => {
      if (project?.type === 'rule' && activeTab === 'rule' && ruleFormRef) {
        ruleFormRef.submit()
      } else if (project?.type === 'code' && activeTab === 'code' && codeFormRef) {
        codeFormRef.submit()
      } else if (project?.type === 'file' && activeTab === 'file' && fileFormRef) {
        fileFormRef.submit()
      } else if (activeTab === 'basic') {
        // 基本信息使用原有的表单提交
        form.submit()
      }
    }

    const isBasicTab = activeTab === 'basic'
    const canSave = isBasicTab || 
                   (project?.type === 'rule' && activeTab === 'rule' && ruleFormRef) ||
                   (project?.type === 'code' && activeTab === 'code' && codeFormRef) ||
                   (project?.type === 'file' && activeTab === 'file' && fileFormRef)

    return (
      <Button
        type="primary"
        loading={loading}
        onClick={handleSave}
        disabled={!canSave}
      >
        保存修改
      </Button>
    )
  }

  return (
    <Card
      title={
        <Space>
          <Button
            type="text"
            icon={<ArrowLeftOutlined />}
            onClick={() => navigate('/projects')}
          >
            返回
          </Button>
          <span>{isEdit ? '编辑项目' : '创建项目'}</span>
        </Space>
      }
      extra={getSaveButton()}
    >
      {isEdit && project?.type === 'rule' ? (
        <Tabs
          activeKey={activeTab}
          onChange={setActiveTab}
          items={[
            {
              key: 'basic',
              label: '基本信息',
              children: renderBasicForm()
            },
            {
              key: 'rule',
              label: '规则配置',
              children: renderRuleForm()
            }
          ]}
        />
      ) : isEdit && project?.type === 'code' ? (
        <Tabs
          activeKey={activeTab}
          onChange={setActiveTab}
          items={[
            {
              key: 'basic',
              label: '基本信息',
              children: renderBasicForm()
            },
            {
              key: 'code',
              label: '代码配置',
              children: renderCodeForm()
            }
          ]}
        />
      ) : isEdit && project?.type === 'file' ? (
        <Tabs
          activeKey={activeTab}
          onChange={setActiveTab}
          items={[
            {
              key: 'basic',
              label: '基本信息',
              children: renderBasicForm()
            },
            {
              key: 'file',
              label: '文件配置',
              children: renderFileForm()
            }
          ]}
        />
      ) : (
        renderBasicForm()
      )}
    </Card>
  )
}

export default ProjectForm
