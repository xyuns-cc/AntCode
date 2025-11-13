import React, { useEffect, useState } from 'react'
import { Card, Form, Input, Select, Button, Space, Tabs, Spin, Alert } from 'antd'
import showNotification from '@/utils/notification'
import { ArrowLeftOutlined, SaveOutlined } from '@ant-design/icons'
import { useParams, useNavigate } from 'react-router-dom'
import { projectService } from '@/services/projects'
import { validationRules } from '@/utils/validators'
import { RuleProjectForm, CodeProjectForm, FileProjectForm } from '@/components/projects'
import envService from '@/services/envs'
import { useAuth } from '@/hooks/useAuth'
import type { Project, ProjectCreateRequest, ProjectUpdateRequest, ExtractionRule } from '@/types'

const { Option } = Select
const { TextArea } = Input

const ProjectForm: React.FC = () => {
  const { id } = useParams<{ id: string }>()
  const navigate = useNavigate()
  const [form] = Form.useForm()
  const [loading, setLoading] = useState(false)
  const [installedInterpreters, setInstalledInterpreters] = useState<Array<{ version: string; source?: string; python_bin: string }>>([])
  const [sharedVenvs, setSharedVenvs] = useState<{ key: string; version: string }[]>([])
  const [venvScope, setVenvScope] = useState<'private' | 'shared'>('private')
  const [dependencies, setDependencies] = useState<string[]>([])
  const [pythonVersion, setPythonVersion] = useState<string>('')
  const [sharedKey, setSharedKey] = useState<string>('')
  const [interpreterSource, setInterpreterSource] = useState<'mise' | 'local'>('mise')
  const [pythonBin, setPythonBin] = useState<string>('')
  const [fetchLoading, setFetchLoading] = useState(false)
  const [project, setProject] = useState<Project | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [activeTab, setActiveTab] = useState('basic')
  
  // 编辑模式下的表单引用
  const [ruleFormRef, setRuleFormRef] = useState<{ submit: () => void } | null>(null)
  const [codeFormRef, setCodeFormRef] = useState<{ submit: () => void } | null>(null)
  const [fileFormRef, setFileFormRef] = useState<{ submit: () => void } | null>(null)
  const { isAuthenticated, loading: authLoading } = useAuth()

  const isEdit = !!id

  useEffect(() => {
    if (isEdit && id && isAuthenticated && !authLoading) {
      const fetchProject = async () => {
        setFetchLoading(true)
        setError(null)
        try {
          const projectData = await projectService.getProject(parseInt(id))
          setProject(projectData)
          form.setFieldsValue({
            name: projectData.name,
            type: projectData.type,
            description: projectData.description,
            tags: Array.isArray(projectData.tags) ? projectData.tags.join(', ') : projectData.tags
          })
        } catch (error: any) {
          const errorMessage = error.response?.data?.message || error.message || '获取项目信息失败'
          setError(errorMessage)
        } finally {
          setFetchLoading(false)
        }
      }
      fetchProject()
    }
    // 初始化加载解释器与共享venv（创建模式）
    if (!isEdit) {
      envService.listInterpreters().then((list) => setInstalledInterpreters(list as any)).catch(() => setInstalledInterpreters([]))
      envService
        .listVenvs({ scope: 'shared', page: 1, size: 100 })
        .then((res) => {
          const items = (res.items || []).map((v) => ({ key: v.key || v.version, version: v.version }))
          setSharedVenvs(items)
        })
        .catch(() => setSharedVenvs([]))
    }
  }, [id, isEdit, form, isAuthenticated, authLoading])

  const handleSubmit = async (values: any) => {
    setLoading(true)
    try {
      if (isEdit && project) {
        const updateData: ProjectUpdateRequest = {
          name: values.name,
          description: values.description,
          tags: typeof values.tags === 'string' ? values.tags : values.tags?.join(', ')
        }
        await projectService.updateProject(project.id, updateData)
        // 成功提示由拦截器统一处理
      } else {
        const createData: ProjectCreateRequest = {
          name: values.name,
          type: values.type,
          description: values.description,
          tags: values.tags,
          venv_scope: venvScope,
          python_version: pythonVersion,
          shared_venv_key: venvScope === 'shared' ? sharedKey || undefined : undefined,
          dependencies,
          // 前端用于API兼容的补充字段：
          // 通过 projects.ts -> formData 追加 'interpreter_source' 和 'python_bin'
          // @ts-expect-error - Backend API expects these fields
          interpreter_source: interpreterSource,
          // @ts-expect-error - Backend API expects these fields
          python_bin: interpreterSource === 'local' ? pythonBin : undefined,
        }
        await projectService.createProject(createData)
        // 成功提示由拦截器统一处理
      }
      navigate('/projects')
    } catch (error) {
      // 错误提示由拦截器统一处理
    } finally {
      setLoading(false)
    }
  }

  const handleRuleSubmit = async (ruleData: any) => {
    setLoading(true)
    try {
      if (isEdit && project) {
        await projectService.updateRuleConfig(project.id, ruleData)
        // 成功提示由拦截器统一处理
        navigate('/projects')
      }
    } catch (error) {
      // 错误提示由拦截器统一处理
    } finally {
      setLoading(false)
    }
  }

  const handleCodeSubmit = async (codeData: any) => {
    setLoading(true)
    try {
      if (isEdit && project) {
        await projectService.updateCodeConfig(project.id, codeData)
        // 成功提示由拦截器统一处理
        navigate('/projects')
      }
    } catch (error) {
      // 错误提示由拦截器统一处理
    } finally {
      setLoading(false)
    }
  }

  const handleFileSubmit = async (fileData: any) => {
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
    } catch (error) {
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
          <Select placeholder="请选择项目类型">
            <Option value="file">文件项目</Option>
            <Option value="rule">规则项目</Option>
            <Option value="code">代码项目</Option>
          </Select>
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

      <Form.Item
        name="venv_scope"
        label="虚拟环境作用域"
        rules={[{ required: true, message: '请选择虚拟环境作用域' }]}
      >
        <Select
          placeholder="请选择环境作用域"
          value={venvScope}
          onChange={(v) => setVenvScope(v)}
          options={[
            { value: 'private', label: '私有（项目专属）' },
            { value: 'shared', label: '公共（共享）' },
          ]}
        />
      </Form.Item>

      <Form.Item
        name="python_version"
        label="Python解释器（来源）"
        rules={[{ required: true, message: '请选择解释器' }]}
      >
        <Select
          showSearch
          placeholder="选择已安装的解释器（local/mise）"
          value={pythonVersion}
          onChange={(val, option: any) => {
            setPythonVersion(val as string)
            setInterpreterSource((option?.source as 'mise' | 'local') || 'mise')
            setPythonBin(option?.python_bin as string)
          }}
          options={(installedInterpreters || []).map((it: any) => ({ value: it.version, label: `${it.version} (${it.source || 'mise'})`, source: it.source || 'mise', python_bin: it.python_bin }))}
          filterOption={(input, option) => ((option?.label as string) || '').toLowerCase().includes(input.toLowerCase())}
        />
      </Form.Item>

      {/* 解释器来源自动随下拉选择，不再手动切换 */}

      {venvScope === 'shared' && (
        <Form.Item
          name="shared_venv_key"
          label="共享环境（从已有列表选择）"
          rules={[{ required: true, message: '请选择共享环境' }]}
        >
          <Select
            placeholder="请选择已有共享环境"
            value={sharedKey}
            onChange={(v) => setSharedKey(v)}
            options={(sharedVenvs || []).map((v) => ({ value: v.key, label: `${v.key} (${v.version})` }))}
          />
        </Form.Item>
      )}

      <Form.Item
        name="dependencies"
        label="依赖（至少一个）"
        rules={[
          {
            validator: async () => {
              if (!dependencies || dependencies.length === 0) throw new Error('请至少添加一个依赖')
            },
          },
        ]}
      >
        <Select
          mode="tags"
          placeholder="输入包名后回车添加，例如: requests==2.32.3"
          value={dependencies}
          onChange={(vals) => setDependencies(vals as string[])}
          tokenSeparators={[',']}
        />
      </Form.Item>

      <Form.Item
        name="tags"
        label="标签"
      >
        <Input placeholder="请输入标签，多个标签用逗号分隔" />
      </Form.Item>

      <Form.Item>
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
      // v2.0.0 统一字段 - 兼容旧数据
      extraction_rules: (() => {
        // 如果有新的extraction_rules字段
        if (project.rule_info.extraction_rules) {
          // 如果是数组格式，需要转换为JSON字符串
          if (Array.isArray(project.rule_info.extraction_rules)) {
            return JSON.stringify(project.rule_info.extraction_rules)
          }
          // 如果已经是字符串格式，直接返回
          return project.rule_info.extraction_rules
        }
        // 否则从旧字段转换
        const oldRules = project.rule_info.list_selectors || project.rule_info.detail_selectors || []
        return JSON.stringify(oldRules)
      })(),
      pagination_config: (() => {
        // 如果有新的pagination_config字段
        if (project.rule_info.pagination_config) {
          // 如果是对象格式，需要转换为JSON字符串
          if (typeof project.rule_info.pagination_config === 'object') {
            return JSON.stringify(project.rule_info.pagination_config)
          }
          // 如果已经是字符串格式，直接返回
          return project.rule_info.pagination_config
        }
        // 否则从旧字段构建
        const config: any = {
          method: project.rule_info.pagination_type || 'none',
          max_pages: project.rule_info.max_pages || 10,
          start_page: project.rule_info.start_page || 1
        }
        // 安全解析pagination_rule
        if (project.rule_info.pagination_rule) {
          try {
            config.next_page_rule = typeof project.rule_info.pagination_rule === 'string' 
              ? JSON.parse(project.rule_info.pagination_rule)
              : project.rule_info.pagination_rule
          } catch (e) {
          }
        }
        return JSON.stringify(config)
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
