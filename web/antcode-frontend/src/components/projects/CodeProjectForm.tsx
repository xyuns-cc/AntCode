import React, { useCallback, useEffect, useReducer, Suspense, lazy } from 'react'
import {
  Form,
  Input,
  Button,
  Space,
  Typography,
  Card,
  Select,
  Upload,
  Tabs,
  Row,
  Col,
  Tag,
  Tooltip,
  Spin
} from 'antd'
import {
  CodeOutlined,
  UploadOutlined,
  PlusOutlined,
  FullscreenOutlined,
  CompressOutlined,
  BulbOutlined
} from '@ant-design/icons'
import type { UploadFile } from 'antd'
import type { RcFile } from 'antd/es/upload/interface'
import { getLanguageOptionsWithIcons, getLanguageConfig } from '@/components/ui/CodeEditor/languages'
import { FileIcon } from '@/utils/fileIcons'
import type { ProjectCreateRequest } from '@/types'

const { Title, Text } = Typography
const { TextArea } = Input
const { Option } = Select
const LazyCodeEditor = lazy(() => import('@/components/ui/CodeEditor'))

// 代码项目表单状态类型
interface CodeProjectFormState {
  fileList: UploadFile[]
  dependencies: string[]
  newDependency: string
  inputMethod: 'editor' | 'upload'
  codeContent: string
  selectedLanguage: string
  isFullscreen: boolean
  showTemplate: boolean
}

// 状态操作类型
type CodeProjectFormAction =
  | { type: 'SET_FILE_LIST'; payload: UploadFile[] }
  | { type: 'SET_DEPENDENCIES'; payload: string[] }
  | { type: 'ADD_DEPENDENCY'; payload: string }
  | { type: 'REMOVE_DEPENDENCY'; payload: number }
  | { type: 'SET_NEW_DEPENDENCY'; payload: string }
  | { type: 'SET_INPUT_METHOD'; payload: 'editor' | 'upload' }
  | { type: 'SET_CODE_CONTENT'; payload: string }
  | { type: 'SET_SELECTED_LANGUAGE'; payload: string }
  | { type: 'TOGGLE_FULLSCREEN' }
  | { type: 'SET_SHOW_TEMPLATE'; payload: boolean }
  | { type: 'RESET_STATE'; payload: Partial<CodeProjectFormState> }

// Reducer函数
const codeProjectFormReducer = (
  state: CodeProjectFormState,
  action: CodeProjectFormAction
): CodeProjectFormState => {
  switch (action.type) {
    case 'SET_FILE_LIST':
      return { ...state, fileList: action.payload }
    case 'SET_DEPENDENCIES':
      return { ...state, dependencies: action.payload }
    case 'ADD_DEPENDENCY':
      return {
        ...state,
        dependencies: [...state.dependencies, action.payload],
        newDependency: ''
      }
    case 'REMOVE_DEPENDENCY':
      return {
        ...state,
        dependencies: state.dependencies.filter((_, index) => index !== action.payload)
      }
    case 'SET_NEW_DEPENDENCY':
      return { ...state, newDependency: action.payload }
    case 'SET_INPUT_METHOD':
      return { ...state, inputMethod: action.payload }
    case 'SET_CODE_CONTENT':
      return { ...state, codeContent: action.payload }
    case 'SET_SELECTED_LANGUAGE':
      return { ...state, selectedLanguage: action.payload }
    case 'TOGGLE_FULLSCREEN':
      return { ...state, isFullscreen: !state.isFullscreen }
    case 'SET_SHOW_TEMPLATE':
      return { ...state, showTemplate: action.payload }
    case 'RESET_STATE':
      return { ...state, ...action.payload }
    default:
      return state
  }
}

// 表单初始数据类型（tags 可以是字符串或数组）
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

const CodeProjectForm: React.FC<CodeProjectFormProps> = ({
  initialData = {},
  onDataChange,
  onSubmit,
  loading: _loading = false,
  isEdit = false,
  onValidationChange,
  onRef
}) => {
  const [form] = Form.useForm()

  // 初始状态
  const initialState: CodeProjectFormState = {
    fileList: [],
    dependencies: initialData.dependencies || [],
    newDependency: '',
    inputMethod: 'editor',
    codeContent: initialData.code_content || '',
    selectedLanguage: initialData.language || 'python',
    isFullscreen: false,
    showTemplate: true
  }

  const [state, dispatch] = useReducer(codeProjectFormReducer, initialState)

  // 获取验证状态
  const getValidationStatus = useCallback(() => {
    if (!isEdit) {
      if (state.inputMethod === 'editor') {
        if (!state.codeContent || state.codeContent.trim() === '') {
          return { isValid: false, tooltip: '请输入代码内容' }
        }
      } else {
        if (state.fileList.length === 0) {
          return { isValid: false, tooltip: '请选择要上传的代码文件' }
        }
      }
    }
    return { isValid: true, tooltip: '' }
  }, [isEdit, state.codeContent, state.fileList, state.inputMethod])

  // 通知父组件验证状态变化
  React.useEffect(() => {
    const { isValid, tooltip } = getValidationStatus()
    onValidationChange?.(isValid, tooltip)
  }, [state.inputMethod, state.codeContent, state.fileList, isEdit, onValidationChange, getValidationStatus])

  // 提供submit方法给父组件
  React.useEffect(() => {
    onRef?.({
      submit: () => {
        form.submit()
      }
    })
  }, [form, onRef])

  // 解构状态
  const { fileList, dependencies, newDependency, inputMethod, codeContent, selectedLanguage, isFullscreen, showTemplate } = state

  // 文件上传配置
  const uploadProps = {
    name: 'code_file',
    multiple: false,
    fileList: state.fileList,
    beforeUpload: (file: RcFile) => {
      const allowedTypes = ['.py', '.js', '.ts', '.java', '.cpp', '.c', '.go', '.rs']
      const fileName = file.name.toLowerCase()
      const isAllowed = allowedTypes.some(type => fileName.endsWith(type))

      if (!isAllowed) {
        return Upload.LIST_IGNORE
      }

      const uploadFile: UploadFile = {
        uid: file.uid,
        name: file.name,
        status: 'done',
        originFileObj: file
      }
      dispatch({ type: 'SET_FILE_LIST', payload: [uploadFile] })
      const updatedData = { ...form.getFieldsValue(), code_file: file }
      onDataChange?.(updatedData)

      return false
    },
    onRemove: () => {
      dispatch({ type: 'SET_FILE_LIST', payload: [] })
      const updatedData = { ...form.getFieldsValue(), code_file: undefined }
      onDataChange?.(updatedData)
    }
  }

  // 添加依赖
  const handleAddDependency = () => {
    if (newDependency.trim() && !dependencies.includes(newDependency.trim())) {
      dispatch({ type: 'ADD_DEPENDENCY', payload: newDependency.trim() })

      const newDeps = [...dependencies, newDependency.trim()]
      const updatedData = { ...form.getFieldsValue(), dependencies: newDeps }
      onDataChange?.(updatedData)
    }
  }

  // 删除依赖
  const handleRemoveDependency = (dep: string) => {
    const depIndex = dependencies.indexOf(dep)
    if (depIndex !== -1) {
      dispatch({ type: 'REMOVE_DEPENDENCY', payload: depIndex })

      const newDeps = dependencies.filter(d => d !== dep)
      const updatedData = { ...form.getFieldsValue(), dependencies: newDeps }
      onDataChange?.(updatedData)
    }
  }

  // 处理代码内容变化
  const handleCodeChange = (value: string | undefined) => {
    const newValue = value || ''
    dispatch({ type: 'SET_CODE_CONTENT', payload: newValue })
    form.setFieldValue('code_content', newValue)

    const updatedData = { ...form.getFieldsValue(), code_content: newValue }
    onDataChange?.(updatedData)
  }

  // 处理语言变化
  const handleLanguageChange = (language: string) => {
    dispatch({ type: 'SET_SELECTED_LANGUAGE', payload: language })
    form.setFieldValue('language', language)

    // 如果启用模板且当前代码为空或为默认模板，则更新为新语言的模板
    if (showTemplate && (!codeContent || isDefaultTemplate(codeContent))) {
      const config = getLanguageConfig(language)
      if (config) {
        const newTemplate = config.defaultTemplate
        dispatch({ type: 'SET_CODE_CONTENT', payload: newTemplate })
        form.setFieldValue('code_content', newTemplate)
      }
    }

    const updatedData = { ...form.getFieldsValue(), language }
    onDataChange?.(updatedData)
  }

  // 检查是否为默认模板
  const isDefaultTemplate = (content: string): boolean => {
    const config = getLanguageConfig(selectedLanguage)
    return config ? content.trim() === config.defaultTemplate.trim() : false
  }

  // 切换全屏模式
  const toggleFullscreen = () => {
    dispatch({ type: 'TOGGLE_FULLSCREEN' })
  }

  // 插入代码模板
  const insertTemplate = () => {
    const config = getLanguageConfig(selectedLanguage)
    if (config) {
      dispatch({ type: 'SET_CODE_CONTENT', payload: config.defaultTemplate })
      form.setFieldValue('code_content', config.defaultTemplate)
    }
  }

  // 初始化代码模板
  useEffect(() => {
    if (!codeContent && showTemplate) {
      const config = getLanguageConfig(selectedLanguage)
      if (config) {
        dispatch({ type: 'SET_CODE_CONTENT', payload: config.defaultTemplate })
        form.setFieldValue('code_content', config.defaultTemplate)
      }
    }
  }, [selectedLanguage, showTemplate, codeContent, form])

  // 监听ESC键退出全屏
  useEffect(() => {
    const handleKeyDown = (event: KeyboardEvent) => {
      if (event.key === 'Escape' && isFullscreen) {
        dispatch({ type: 'TOGGLE_FULLSCREEN' })
      }
    }

    if (isFullscreen) {
      document.addEventListener('keydown', handleKeyDown)
      document.body.style.overflow = 'hidden'
    } else {
      document.body.style.overflow = 'auto'
    }

    return () => {
      document.removeEventListener('keydown', handleKeyDown)
      document.body.style.overflow = 'auto'
    }
  }, [isFullscreen])

  // 表单提交
  const handleFinish = (values: ProjectCreateRequest) => {
    if (isEdit) {
      // 编辑模式：只提交代码配置相关的字段
      const submitData: Record<string, unknown> = {
        language: values.language,
        version: values.version,
        entry_point: values.entry_point,
        documentation: values.documentation,
        code_content: inputMethod === 'editor' ? codeContent : undefined,
      }
      onSubmit(submitData)
    } else {
      // 创建模式：提交完整的项目数据
      const submitData: Record<string, unknown> = {
        ...values,
        type: 'code',
        dependencies,
        code_content: inputMethod === 'editor' ? codeContent : undefined,
        code_file: inputMethod === 'upload' ? fileList[0]?.originFileObj : undefined,
        tags: Array.isArray(values.tags)
          ? values.tags
          : (values.tags || '')
              .split(',')
              .map((tag: string) => tag.trim())
              .filter(Boolean)
      }
      onSubmit(submitData)
    }
  }

  // 表单值变化
  const handleValuesChange = (
    _changedValues: Partial<ProjectCreateRequest>,
    allValues: ProjectCreateRequest
  ) => {
    const updatedData = {
      ...allValues,
      dependencies,
      code_content: inputMethod === 'editor' ? codeContent : undefined,
      code_file: inputMethod === 'upload' ? fileList[0]?.originFileObj : undefined
    }
    onDataChange?.(updatedData)
  }

  const tabItems = [
    {
      key: 'basic',
      label: '基本信息',
      children: (
        <Space direction="vertical" style={{ width: '100%' }} size="middle">
          <Row gutter={16}>
            <Col span={12}>
              <Form.Item
                name="name"
                label="项目名称"
                rules={[
                  { required: true, message: '请输入项目名称' },
                  { min: 3, max: 50, message: '项目名称长度为3-50个字符' }
                ]}
              >
                <Input placeholder="请输入项目名称" />
              </Form.Item>
            </Col>
            <Col span={12}>
              <Form.Item
                name="language"
                label="编程语言"
                initialValue="python"
                rules={[{ required: true, message: '请选择编程语言' }]}
              >
                <Select
                  value={selectedLanguage}
                  onChange={handleLanguageChange}
                  showSearch
                  placeholder="选择编程语言"
                  optionFilterProp="children"
                >
                  {getLanguageOptionsWithIcons().map(option => (
                    <Option key={option.value} value={option.value}>
                      <div style={{ 
                        display: 'flex', 
                        alignItems: 'center', 
                        gap: '8px'
                      }}>
                        <FileIcon 
                          extension={option.extension}
                          size={16}
                        />
                        <span style={{ 
                          color: option.color, 
                          lineHeight: '16px',
                          fontSize: '14px'
                        }}>
                          {option.label}
                        </span>
                      </div>
                    </Option>
                  ))}
                </Select>
              </Form.Item>
            </Col>
          </Row>

          <Row gutter={16}>
            <Col span={12}>
              <Form.Item
                name="version"
                label="版本号"
                initialValue="1.0.0"
              >
                <Input placeholder="例如: 1.0.0" />
              </Form.Item>
            </Col>
            <Col span={12}>
              <Form.Item
                name="tags"
                label="项目标签"
                tooltip="多个标签用逗号分隔"
              >
                <Input placeholder="例如: 工具,脚本,自动化" />
              </Form.Item>
            </Col>
          </Row>

          <Form.Item
            name="description"
            label="项目描述"
          >
            <TextArea
              rows={3}
              placeholder="请描述代码的功能和用途"
              maxLength={500}
              showCount
            />
          </Form.Item>

          <Form.Item
            name="code_entry_point"
            label="入口函数"
            tooltip="指定代码的主入口函数，如 main"
          >
            <Input placeholder="例如: main, run, execute" />
          </Form.Item>
        </Space>
      )
    },
    {
      key: 'code',
      label: '代码内容',
      children: (
        <Space direction="vertical" style={{ width: '100%' }} size="middle">
          <Card title="代码输入方式" size="small">
            <Select
              value={inputMethod}
              onChange={(value) => dispatch({ type: 'SET_INPUT_METHOD', payload: value })}
              style={{ width: '100%' }}
            >
              <Option value="editor">在线编辑器</Option>
              <Option value="upload">上传文件</Option>
            </Select>
          </Card>

          {inputMethod === 'editor' ? (
            <div>
              <div style={{
                display: 'flex',
                justifyContent: 'space-between',
                alignItems: 'center',
                marginBottom: 12
              }}>
                <span style={{ fontWeight: 500 }}>
                  代码内容 <span style={{ color: '#ff4d4f' }}>*</span>
                </span>
                <Space>
                  <Tooltip title="插入代码模板" placement="top">
                    <Button
                      type="text"
                      icon={<BulbOutlined />}
                      onClick={insertTemplate}
                      size="small"
                    >
                      模板
                    </Button>
                  </Tooltip>
                  <Tooltip title={isFullscreen ? "退出全屏" : "全屏编辑"} placement="top">
                    <Button
                      type="text"
                      icon={isFullscreen ? <CompressOutlined /> : <FullscreenOutlined />}
                      onClick={toggleFullscreen}
                      size="small"
                    />
                  </Tooltip>
                </Space>
              </div>

              <Form.Item
                name="code_content"
                rules={[{ required: true, message: '请输入代码内容' }]}
                style={{ marginBottom: 0 }}
              >
                <div style={isFullscreen ? {
                  position: 'fixed',
                  top: 0,
                  left: 0,
                  right: 0,
                  bottom: 0,
                  zIndex: 1000,
                  backgroundColor: '#fff',
                  padding: 20
                } : {}}>
                  {isFullscreen && (
                    <div style={{
                      display: 'flex',
                      justifyContent: 'space-between',
                      alignItems: 'center',
                      marginBottom: 16,
                      paddingBottom: 16,
                      borderBottom: '1px solid #f0f0f0'
                    }}>
                      <h3 style={{ margin: 0 }}>
                        <CodeOutlined style={{ marginRight: 8 }} />
                        代码编辑器 - {getLanguageConfig(selectedLanguage)?.name}
                      </h3>
                      <Button
                        type="primary"
                        icon={<CompressOutlined />}
                        onClick={toggleFullscreen}
                      >
                        退出全屏
                      </Button>
                    </div>
                  )}

                  <Suspense
                    fallback={(
                      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'center', height: isFullscreen ? 'calc(100vh - 120px)' : 500 }}>
                        <Spin tip="加载代码编辑器...">
                          <div style={{ height: 200, width: '100%' }} />
                        </Spin>
                      </div>
                    )}
                  >
                    <LazyCodeEditor
                    value={codeContent}
                    language={selectedLanguage}
                    onChange={handleCodeChange}
                    height={isFullscreen ? 'calc(100vh - 120px)' : 500}
                    placeholder={`请输入${getLanguageConfig(selectedLanguage)?.name}代码...`}
                  />
                  </Suspense>
                </div>
              </Form.Item>
            </div>
          ) : (
            <Form.Item
              label="代码文件"
              required
              help="支持 .py、.js、.ts、.java、.cpp、.c、.go、.rs 等格式"
            >
              <Upload.Dragger {...uploadProps}>
                <p className="ant-upload-drag-icon">
                  <UploadOutlined style={{ fontSize: 48, color: '#722ed1' }} />
                </p>
                <p className="ant-upload-text">
                  点击或拖拽代码文件到此区域上传
                </p>
                <p className="ant-upload-hint">
                  支持单个代码文件上传
                </p>
              </Upload.Dragger>
            </Form.Item>
          )}
        </Space>
      )
    },
    {
      key: 'deps',
      label: '依赖管理',
      children: (
        <Space direction="vertical" style={{ width: '100%' }} size="middle">
          <Card title="依赖包管理" size="small">
            <Space.Compact style={{ width: '100%', marginBottom: 16 }}>
              <Input
                placeholder="输入依赖包名"
                value={newDependency}
                onChange={(e) => dispatch({ type: 'SET_NEW_DEPENDENCY', payload: e.target.value })}
                onPressEnter={handleAddDependency}
              />
              <Button
                type="primary"
                icon={<PlusOutlined />}
                onClick={handleAddDependency}
                disabled={!newDependency.trim()}
              >
                添加
              </Button>
            </Space.Compact>

            {dependencies.length > 0 && (
              <div>
                <Text strong style={{ marginBottom: 8, display: 'block' }}>
                  已添加的依赖包:
                </Text>
                {dependencies.map((dep) => (
                  <Tag
                    key={dep}
                    closable
                    onClose={() => handleRemoveDependency(dep)}
                    style={{ marginBottom: 4 }}
                  >
                    {dep}
                  </Tag>
                ))}
              </div>
            )}
          </Card>

          <Form.Item
            name="documentation"
            label="代码文档"
          >
            <TextArea
              rows={8}
              placeholder="请添加代码的使用说明、API文档等..."
              maxLength={2000}
              showCount
            />
          </Form.Item>
        </Space>
      )
    }
  ]

  return (
    <div>
      <div style={{ textAlign: 'center', marginBottom: 24 }}>
        <Title level={4}>
          <CodeOutlined style={{ marginRight: 8, color: '#722ed1' }} />
          代码项目配置
        </Title>
        <Text type="secondary">
          直接编写或上传源代码，支持多种编程语言
        </Text>
      </div>

      <Form
        form={form}
        layout="vertical"
        initialValues={initialData}
        onFinish={handleFinish}
        onValuesChange={handleValuesChange}
      >
        <Tabs
          items={tabItems}
          type="card"
          size="small"
        />


      </Form>
    </div>
  )
}

// 使用React.memo优化，避免不必要的重渲染
export default React.memo(CodeProjectForm)
