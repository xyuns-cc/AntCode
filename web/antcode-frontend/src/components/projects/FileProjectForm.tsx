import React, { useCallback, useState } from 'react'
import {
  Form,
  Input,
  Upload,
  Button,
  Space,
  Typography,
  Alert,
  Tag,
  Divider,
  Row,
  Col,
  Card,
  
} from 'antd'
import showNotification from '@/utils/notification'
import {
  UploadOutlined,
  FileOutlined,
  PlusOutlined
} from '@ant-design/icons'
import type { UploadFile, UploadProps } from 'antd'
import type { ProjectCreateRequest } from '@/types'

const { Title, Text } = Typography
const { TextArea } = Input

// 文件大小格式化函数
const formatFileSize = (bytes: number | string): string => {
  // 确保转换为数字
  const numBytes = typeof bytes === 'string' ? parseInt(bytes, 10) : bytes

  if (!numBytes || numBytes === 0) return '0 B'

  const k = 1024
  const sizes = ['B', 'KB', 'MB', 'GB']
  const i = Math.floor(Math.log(numBytes) / Math.log(k))

  return parseFloat((numBytes / Math.pow(k, i)).toFixed(2)) + ' ' + sizes[i]
}

const isCompressedFile = (filename?: string): boolean => {
  const name = (filename || '').toLowerCase()
  return name.endsWith('.zip') || name.endsWith('.tar.gz') || name.endsWith('.tar')
}

// 表单初始数据类型（tags 可以是字符串或数组，file_info 用于编辑模式）
interface FileProjectFormInitialData extends Omit<Partial<ProjectCreateRequest>, 'tags'> {
  tags?: string | string[]
  file_info?: {
    original_name: string
    file_size: number
    file_hash: string
    file_path?: string
    entry_point?: string
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

const FileProjectForm: React.FC<FileProjectFormProps> = ({
  initialData = {},
  onDataChange,
  onSubmit,
  loading: _loading = false,
  isEdit = false,
  onValidationChange,
  onRef
}) => {
  const [form] = Form.useForm()
  const [fileList, setFileList] = useState<UploadFile[]>([])
  const [additionalFiles, setAdditionalFiles] = useState<UploadFile[]>([])
  const [dependencies, setDependencies] = useState<string[]>(initialData.dependencies || [])
  const [newDependency, setNewDependency] = useState('')
  const entryPointValue = Form.useWatch('entry_point', form)

  // 获取验证状态
  const getValidationStatus = useCallback(() => {
    if (!isEdit && fileList.length === 0) {
      return { isValid: false, tooltip: '请选择要上传的文件' }
    }
    if (fileList.length > 0 && isCompressedFile(fileList[0]?.name)) {
      if (!entryPointValue || (typeof entryPointValue === 'string' && entryPointValue.trim() === '')) {
        return { isValid: false, tooltip: '压缩包必须填写入口文件' }
      }
    }
    return { isValid: true, tooltip: '' }
  }, [entryPointValue, fileList, isEdit])

  // 通知父组件验证状态变化
  React.useEffect(() => {
    const { isValid, tooltip } = getValidationStatus()
    onValidationChange?.(isValid, tooltip)
  }, [entryPointValue, fileList, isEdit, onValidationChange, getValidationStatus])

  // 提供submit方法给父组件
  React.useEffect(() => {
    onRef?.({
      submit: () => {
        form.submit()
      }
    })
  }, [form, onRef])

  // 主文件上传配置
  const uploadProps: UploadProps = {
    name: 'file',
    multiple: false,
    fileList,
    beforeUpload: (file) => {
      // 检查文件类型
      const allowedTypes = ['.py', '.zip', '.tar.gz', '.tar']
      const fileName = file.name.toLowerCase()
      const isAllowed = allowedTypes.some(type => fileName.endsWith(type))
      
      if (!isAllowed) {
        showNotification('error', '只支持 .py、.zip、.tar.gz、.tar 格式的文件')
        return Upload.LIST_IGNORE
      }

      // 检查文件大小 (100MB)
      const isLt100M = file.size / 1024 / 1024 < 100
      if (!isLt100M) {
        showNotification('error', '文件大小不能超过 100MB')
        return Upload.LIST_IGNORE
      }

      setFileList([file])
      
      // 更新表单数据
      const updatedData = { ...form.getFieldsValue(), file }
      onDataChange?.(updatedData)
      
      return false // 阻止自动上传
    },
    onRemove: () => {
      setFileList([])
      const updatedData = { ...form.getFieldsValue(), file: undefined }
      onDataChange?.(updatedData)
    },
    showUploadList: {
      showRemoveIcon: true,
      showPreviewIcon: false
    }
  }

  // 附加文件上传配置
  const additionalUploadProps: UploadProps = {
    name: 'files',
    multiple: true,
    fileList: additionalFiles,
    beforeUpload: (file) => {
      // 检查文件类型 - 附加文件支持更多类型
      const allowedTypes = ['.py', '.json', '.txt', '.md', '.yml', '.yaml', '.xml', '.csv', '.sql', '.sh', '.bat', '.cfg', '.ini', '.conf']
      const fileName = file.name.toLowerCase()
      const isAllowed = allowedTypes.some(type => fileName.endsWith(type))
      
      if (!isAllowed) {
        showNotification('error', `文件 ${file.name} 类型不支持，支持的类型：${allowedTypes.join(', ')}`)
        return Upload.LIST_IGNORE
      }

      // 检查文件大小 (10MB per file)
      const isLt10M = file.size / 1024 / 1024 < 10
      if (!isLt10M) {
        showNotification('error', `文件 ${file.name} 大小不能超过 10MB`)
        return Upload.LIST_IGNORE
      }

      // 检查重复文件名
      const isDuplicate = additionalFiles.some(f => f.name === file.name)
      if (isDuplicate) {
        showNotification('warning', `文件 ${file.name} 已存在`)
        return Upload.LIST_IGNORE
      }

      const newAdditionalFiles = [...additionalFiles, file]
      setAdditionalFiles(newAdditionalFiles)
      
      // 更新表单数据
      const updatedData = { ...form.getFieldsValue(), additionalFiles: newAdditionalFiles }
      onDataChange?.(updatedData)
      
      return false // 阻止自动上传
    },
    onRemove: (file) => {
      const newAdditionalFiles = additionalFiles.filter(f => f.uid !== file.uid)
      setAdditionalFiles(newAdditionalFiles)
      const updatedData = { ...form.getFieldsValue(), additionalFiles: newAdditionalFiles }
      onDataChange?.(updatedData)
    },
    showUploadList: {
      showRemoveIcon: true,
      showPreviewIcon: false
    }
  }

  // 添加依赖
  const handleAddDependency = () => {
    if (newDependency.trim() && !dependencies.includes(newDependency.trim())) {
      const newDeps = [...dependencies, newDependency.trim()]
      setDependencies(newDeps)
      setNewDependency('')
      
      const updatedData = { ...form.getFieldsValue(), dependencies: newDeps }
      onDataChange?.(updatedData)
    }
  }

  // 删除依赖
  const handleRemoveDependency = (dep: string) => {
    const newDeps = dependencies.filter(d => d !== dep)
    setDependencies(newDeps)
    
    const updatedData = { ...form.getFieldsValue(), dependencies: newDeps }
    onDataChange?.(updatedData)
  }

  // 表单提交
  const handleFinish = (values: ProjectCreateRequest) => {
    // 创建模式下必须有文件
    if (!isEdit && !fileList[0]) {
      showNotification('error', '请上传项目文件')
      return
    }

    if (fileList[0] && isCompressedFile(fileList[0].name)) {
      if (!values.entry_point || values.entry_point.trim() === '') {
        showNotification('error', '压缩包必须指定入口文件')
        return
      }
    }

    const mainFile = fileList[0]?.originFileObj ?? fileList[0]
    const extraFiles = additionalFiles.map((f) => f.originFileObj ?? f)

    const submitData: Record<string, unknown> = {
      ...values,
      type: 'file',
      file: mainFile as File | undefined,  // 编辑模式下可选文件
      additionalFiles: extraFiles as File[], // 附加文件列表
      dependencies,
      tags: Array.isArray(values.tags)
        ? values.tags
        : (values.tags || '')
            .split(',')
            .map((tag: string) => tag.trim())
            .filter(Boolean)
    }

    onSubmit(submitData)
  }

  // 表单值变化
  const handleValuesChange = (
    _changedValues: Partial<ProjectCreateRequest>,
    allValues: ProjectCreateRequest
  ) => {
    const updatedData = { 
      ...allValues, 
      dependencies, 
      file: fileList[0]?.originFileObj,
      additionalFiles: additionalFiles.map(f => f.originFileObj || f)
    }
    onDataChange?.(updatedData)
  }

  return (
    <div>
      <div style={{ textAlign: 'center', marginBottom: 24 }}>
        <Title level={4}>
          <FileOutlined style={{ marginRight: 8, color: '#1890ff' }} />
          {isEdit ? '编辑文件项目' : '文件项目配置'}
        </Title>
        <Text type="secondary">
          {isEdit
            ? '修改文件项目的配置信息'
            : '上传您的项目文件，支持 Python 文件、ZIP 压缩包等格式'
          }
        </Text>
      </div>

      <Form
        form={form}
        layout="vertical"
        initialValues={initialData}
        onFinish={handleFinish}
        onValuesChange={handleValuesChange}
      >
        {/* 基本信息 */}
        <Card title="基本信息" size="small" style={{ marginBottom: 16 }}>
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
                name="tags"
                label="项目标签"
                tooltip="多个标签用逗号分隔"
              >
                <Input placeholder="例如: 爬虫,数据处理,自动化" />
              </Form.Item>
            </Col>
          </Row>

          <Form.Item
            name="description"
            label="项目描述"
          >
            <TextArea
              rows={3}
              placeholder="请描述项目的功能和用途"
              maxLength={500}
              showCount
            />
          </Form.Item>
        </Card>

        {/* 文件上传 - 编辑模式下隐藏 */}
        {!isEdit && (
          <Card title="文件上传" size="small" style={{ marginBottom: 16 }}>
            <Form.Item
              label="项目文件"
              required
              help="支持 .py、.zip、.tar.gz、.tar 格式，最大 100MB"
            >
              <Upload.Dragger {...uploadProps} style={{ padding: '20px 0' }}>
                <p className="ant-upload-drag-icon">
                  <UploadOutlined style={{ fontSize: 48, color: '#1890ff' }} />
                </p>
                <p className="ant-upload-text">
                  点击或拖拽文件到此区域上传
                </p>
                <p className="ant-upload-hint">
                  支持单个文件上传，支持 Python 文件和压缩包
                </p>
              </Upload.Dragger>
            </Form.Item>

            {fileList.length > 0 && (
              <Alert
                message="主文件上传成功"
                description={`已选择文件: ${fileList[0].name} (${formatFileSize(fileList[0].size || 0)})`}
                type="success"
                showIcon
                style={{ marginTop: 8 }}
              />
            )}

            {/* 附加文件上传 */}
            <Divider orientation="left">附加文件（可选）</Divider>
            <Form.Item
              label="附加文件"
              help="支持配置文件、文档等。支持 .py, .json, .txt, .md, .yml, .xml 等格式，单个文件最大 10MB"
            >
              <Upload {...additionalUploadProps}>
                <Button icon={<PlusOutlined />}>添加附加文件</Button>
              </Upload>
            </Form.Item>

            {additionalFiles.length > 0 && (
              <Alert
                message={`已添加 ${additionalFiles.length} 个附加文件`}
                description={
                  <div>
                    {additionalFiles.map((file, index) => (
                      <div key={file.uid} style={{ marginBottom: index < additionalFiles.length - 1 ? 4 : 0 }}>
                        <Text>
                          {file.name} ({formatFileSize(file.size || 0)})
                        </Text>
                      </div>
                    ))}
                  </div>
                }
                type="info"
                showIcon
                style={{ marginTop: 8 }}
              />
            )}
          </Card>
        )}

        {/* 编辑模式下显示当前文件信息和替换选项 */}
        {isEdit && initialData.file_info && (
          <Card title="文件管理" size="small" style={{ marginBottom: 16 }}>
            <Alert
              message="当前文件信息"
              description={
                <div>
                  <p><strong>文件名:</strong> {initialData.file_info.original_name}</p>
                  <p><strong>文件大小:</strong> {formatFileSize(initialData.file_info.file_size)}</p>
                </div>
              }
              type="info"
              showIcon
              style={{ marginBottom: 16 }}
            />

            <Form.Item
              label="替换文件"
              help="可选：上传新文件来替换当前文件。支持 .py、.zip、.tar.gz、.tar 格式，最大 100MB"
            >
              <Upload.Dragger {...uploadProps} style={{ padding: '20px 0' }}>
                <p className="ant-upload-drag-icon">
                  <UploadOutlined style={{ fontSize: 48, color: '#1890ff' }} />
                </p>
                <p className="ant-upload-text">
                  点击或拖拽文件到此区域替换当前文件
                </p>
                <p className="ant-upload-hint">
                  可选操作：如不上传新文件，将保持当前文件不变
                </p>
              </Upload.Dragger>
            </Form.Item>

            {fileList.length > 0 && (
              <Alert
                message="新文件已选择"
                description={`将替换为: ${fileList[0].name}`}
                type="success"
                showIcon
                style={{ marginTop: 8 }}
              />
            )}
          </Card>
        )}

        {/* 运行配置 */}
        <Card title="运行配置" size="small" style={{ marginBottom: 16 }}>
          <Form.Item
            name="entry_point"
            label="入口文件"
            tooltip="指定项目的主入口文件，如 main.py"
            extra="压缩包必须填写入口文件；单文件项目可留空自动使用文件名"
          >
            <Input placeholder="例如: main.py" />
          </Form.Item>

          <Row gutter={16}>
            <Col span={12}>
              <Form.Item
                name="runtime_config"
                label="运行时配置"
                tooltip='JSON格式的运行时配置，如 {"max_workers": 4}'
              >
                <TextArea
                  rows={3}
                  placeholder='{"max_workers": 4, "timeout": 30}'
                />
              </Form.Item>
            </Col>
            <Col span={12}>
              <Form.Item
                name="environment_vars"
                label="环境变量"
                tooltip='JSON格式的环境变量，如 {"API_KEY": "your_key"}'
              >
                <TextArea
                  rows={3}
                  placeholder='{"API_KEY": "your_key", "DEBUG": "true"}'
                />
              </Form.Item>
            </Col>
          </Row>

          {/* 依赖管理 */}
          <Form.Item label="Python 依赖包">
            <Space direction="vertical" style={{ width: '100%' }}>
              <Space.Compact style={{ width: '100%' }}>
                <Input
                  placeholder="输入依赖包名，如 requests"
                  value={newDependency}
                  onChange={(e) => setNewDependency(e.target.value)}
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
                <div style={{ marginTop: 8 }}>
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
            </Space>
          </Form.Item>
        </Card>


      </Form>
    </div>
  )
}

// 使用React.memo优化，避免不必要的重渲染
export default React.memo(FileProjectForm)
