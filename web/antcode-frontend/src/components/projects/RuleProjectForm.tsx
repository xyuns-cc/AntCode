import React, { useState } from 'react'
import {
  Form,
  Input,
  Button,
  Space,
  Typography,
  Card,
  Select,
  InputNumber,
  Tabs,
  Row,
  Col,
  Divider,
  Alert,
  Tooltip,
  Tag,
  Radio
} from 'antd'
import {
  SettingOutlined,
  GlobalOutlined,
  RocketOutlined,
  SafetyOutlined,
  ThunderboltOutlined,
  ApiOutlined,
  BugOutlined,
  UnorderedListOutlined,
  FileTextOutlined,
  AppstoreOutlined
} from '@ant-design/icons'
import { useThemeContext } from '@/contexts/ThemeContext'
import RuleSelector from './RuleSelector'
import type { ProjectCreateRequest, ExtractionRule, PaginationConfig, ProxyConfig, AntiSpiderConfig, TaskConfig } from '@/types'

const { Title, Text } = Typography
const { TextArea } = Input
const { Option } = Select

interface RuleProjectFormProps {
  initialData?: Partial<ProjectCreateRequest>
  onDataChange?: (data: Partial<ProjectCreateRequest>) => void
  onSubmit: (data: ProjectCreateRequest) => void
  loading?: boolean
  isEdit?: boolean
  onValidationChange?: (isValid: boolean, tooltip: string) => void
  onRef?: (ref: { submit: () => void }) => void
}

// 采集引擎配置
const CRAWL_ENGINES = [
  {
    value: 'requests',
    label: 'Requests (轻量快速)',
    icon: <RocketOutlined />,
    color: 'blue',
    description: '适合静态网页、API接口',
    features: ['轻量快速', '资源消耗低', '适合大批量采集'],
    scenarios: ['新闻网站', '博客', 'API接口']
  },
  {
    value: 'browser',
    label: 'Browser (浏览器引擎)',
    icon: <GlobalOutlined />,
    color: 'green',
    description: '支持JavaScript渲染',
    features: ['支持JS渲染', '可处理复杂交互', '模拟真实浏览器'],
    scenarios: ['SPA应用', 'React/Vue网站', '需要登录的网站']
  },
  {
    value: 'curl_cffi',
    label: 'Curl CFFI (反检测)',
    icon: <SafetyOutlined />,
    color: 'orange',
    description: '强大的反爬虫能力',
    features: ['模拟真实curl请求', '反检测能力强', 'TLS指纹伪装'],
    scenarios: ['电商网站', '有反爬虫的网站', '需要绕过CF的网站']
  }
]

const RuleProjectForm: React.FC<RuleProjectFormProps> = ({
  initialData = {},
  onDataChange,
  onSubmit,
  loading = false,
  isEdit = false,
  onValidationChange,
  onRef
}) => {
  const [form] = Form.useForm()
  const { isDark } = useThemeContext()
  const [listRules, setListRules] = useState<ExtractionRule[]>([])
  const [detailRules, setDetailRules] = useState<ExtractionRule[]>([])
  const [paginationConfig, setPaginationConfig] = useState<PaginationConfig>(() => {
    if (!initialData.pagination_config) {
      return { method: 'none', max_pages: 10, start_page: 1 }
    }
    try {
      return JSON.parse(initialData.pagination_config)
    } catch (e) {
      return { method: 'none', max_pages: 10, start_page: 1 }
    }
  })
  const [selectedEngine, setSelectedEngine] = useState(initialData.engine || 'requests')
  const [callbackType, setCallbackType] = useState<'list' | 'detail' | 'mixed'>('mixed')
  
  // v2.0.0 新增状态 - 安全解析JSON
  const [proxyConfig, setProxyConfig] = useState<ProxyConfig>(() => {
    if (!initialData.proxy_config) {
      return { enabled: false, proxy_type: 'http' }
    }
    try {
      return JSON.parse(initialData.proxy_config)
    } catch (e) {
      return { enabled: false, proxy_type: 'http' }
    }
  })
  
  const [antiSpiderConfig, setAntiSpiderConfig] = useState<AntiSpiderConfig>(() => {
    if (!initialData.anti_spider) {
      return { enabled: false, user_agent_rotation: false, random_delay: false }
    }
    try {
      return JSON.parse(initialData.anti_spider)
    } catch (e) {
      return { enabled: false, user_agent_rotation: false, random_delay: false }
    }
  })
  
  const [taskConfig, setTaskConfig] = useState<TaskConfig>(() => {
    if (!initialData.task_config) {
      return { queue_priority: 0, concurrency_limit: 1 }
    }
    try {
      return JSON.parse(initialData.task_config)
    } catch (e) {
      return { queue_priority: 0, concurrency_limit: 1 }
    }
  })

  // 获取按钮禁用状态
  const getButtonDisabled = () => {
    if (callbackType === 'mixed') {
      return listRules.length === 0 || detailRules.length === 0
    } else if (callbackType === 'list') {
      return listRules.length === 0
    } else if (callbackType === 'detail') {
      return detailRules.length === 0
    }
    return true
  }

  // 获取按钮提示文本
  const getButtonTooltip = () => {
    if (callbackType === 'mixed') {
      if (listRules.length === 0 && detailRules.length === 0) {
        return '请配置列表页和详情页提取规则'
      } else if (listRules.length === 0) {
        return '请配置列表页提取规则'
      } else if (detailRules.length === 0) {
        return '请配置详情页提取规则'
      }
    } else if (callbackType === 'list' && listRules.length === 0) {
      return '请配置列表页提取规则'
    } else if (callbackType === 'detail' && detailRules.length === 0) {
      return '请配置详情页提取规则'
    }
    return ''
  }

  // 初始化callbackType
  React.useEffect(() => {
    if (initialData.callback_type) {
      setCallbackType(initialData.callback_type)
    }
  }, [initialData.callback_type])

  // 初始化规则
  React.useEffect(() => {
    if (initialData.extraction_rules) {
      try {
        const rules = JSON.parse(initialData.extraction_rules)
        
        if (Array.isArray(rules)) {
          // 根据规则的page_type分离到不同的状态
          const listRulesFromData = rules.filter((rule: ExtractionRule) => 
            rule.page_type === 'list' || (!rule.page_type && callbackType === 'list')
          )
          const detailRulesFromData = rules.filter((rule: ExtractionRule) => 
            rule.page_type === 'detail' || (!rule.page_type && callbackType === 'detail')
          )
          
          setListRules(listRulesFromData)
          setDetailRules(detailRulesFromData)
        }
      } catch (e) {
        // 解析extraction_rules失败，使用默认值
        setListRules([])
        setDetailRules([])
      }
    }
  }, [initialData.extraction_rules, callbackType])

  // 通知父组件验证状态变化
  React.useEffect(() => {
    const isValid = !getButtonDisabled()
    const tooltip = getButtonTooltip()
    onValidationChange?.(isValid, tooltip)
  }, [listRules, detailRules, callbackType, onValidationChange])

  // 提供submit方法给父组件
  React.useEffect(() => {
    onRef?.({
      submit: () => {
        form.submit()
      }
    })
  }, [form, onRef])

  // 动态样式函数
  const getIconStyle = () => ({
    marginRight: 8,
    color: isDark ? '#52c41a' : '#52c41a'
  })

  // 表单提交
  const handleFinish = (values: any) => {
    // 处理headers和cookies，根据API文档，统一API支持对象和字符串两种格式
    let processedHeaders = values.headers
    let processedCookies = values.cookies
    
    // 如果是字符串，尝试解析为对象（符合API文档要求）
    if (processedHeaders && typeof processedHeaders === 'string') {
      try {
        processedHeaders = JSON.parse(processedHeaders)
      } catch (e) {
        // 解析失败时保持字符串格式，API会自动处理
      }
    }
    
    if (processedCookies && typeof processedCookies === 'string') {
      try {
        processedCookies = JSON.parse(processedCookies)
      } catch (e) {
        // 解析失败时保持字符串格式，API会自动处理
      }
    }

    // 根据回调类型合并规则
    let allRules: ExtractionRule[] = []
    if (callbackType === 'mixed') {
      // 混合模式：合并两种规则，并设置page_type
      const listRulesWithType = listRules.map(rule => ({ ...rule, page_type: 'list' as const }))
      const detailRulesWithType = detailRules.map(rule => ({ ...rule, page_type: 'detail' as const }))
      allRules = [...listRulesWithType, ...detailRulesWithType]
    } else if (callbackType === 'list') {
      allRules = listRules
    } else if (callbackType === 'detail') {
      allRules = detailRules
    }

    // 验证规则不能为空
    if (allRules.length === 0) {
      return
    }

    // 如果是混合模式，验证必须有列表页和详情页规则
    if (callbackType === 'mixed') {
      if (listRules.length === 0 || detailRules.length === 0) {
        return
      }
    }

    // 清理规则数据，只保留API需要的字段
    const cleanedRules = allRules.map(rule => ({
      desc: rule.desc,
      type: rule.type,
      expr: rule.expr,
      ...(rule.page_type && { page_type: rule.page_type })
    }))

    const submitData: ProjectCreateRequest = {
      ...values,
      type: 'rule',
      callback_type: callbackType,
      extraction_rules: JSON.stringify(cleanedRules), // API文档要求JSON字符串格式
      pagination_config: JSON.stringify(paginationConfig), // API文档要求JSON字符串格式
      headers: processedHeaders, // 支持对象和字符串两种格式
      cookies: processedCookies, // 支持对象和字符串两种格式
      tags: values.tags?.split(',').map((tag: string) => tag.trim()).filter(Boolean) || [],
      request_delay: values.request_delay ? Math.round(values.request_delay * 1000) : 1000 // 转换为毫秒整数
    }
    
    onSubmit(submitData)
  }

  // 表单值变化
  const handleValuesChange = (changedValues: any, allValues: any) => {
    // 根据回调类型合并规则
    let allRules: ExtractionRule[] = []
    if (callbackType === 'mixed') {
      const listRulesWithType = listRules.map(rule => ({ ...rule, page_type: 'list' as const }))
      const detailRulesWithType = detailRules.map(rule => ({ ...rule, page_type: 'detail' as const }))
      allRules = [...listRulesWithType, ...detailRulesWithType]
    } else if (callbackType === 'list') {
      allRules = listRules
    } else if (callbackType === 'detail') {
      allRules = detailRules
    }
    
    const updatedData = { 
      ...allValues, 
      callback_type: callbackType,
      extraction_rules: JSON.stringify(allRules),
      pagination_config: JSON.stringify(paginationConfig)
    }
    onDataChange?.(updatedData)
  }

  // 列表规则变化
  const handleListRulesChange = (rules: ExtractionRule[]) => {
    setListRules(rules)
    // 使用新的规则值而不是依赖状态，避免React状态更新的异步问题
    const updatedData = getUpdatedData(rules, detailRules)
    onDataChange?.(updatedData)
  }

  // 详情规则变化
  const handleDetailRulesChange = (rules: ExtractionRule[]) => {
    setDetailRules(rules)
    // 使用新的规则值而不是依赖状态，避免React状态更新的异步问题
    const updatedData = getUpdatedData(listRules, rules)
    onDataChange?.(updatedData)
  }

  // 获取更新后的数据
  const getUpdatedData = (currentListRules: ExtractionRule[], currentDetailRules: ExtractionRule[]) => {
    // 根据回调类型合并规则
    let allRules: ExtractionRule[] = []
    if (callbackType === 'mixed') {
      const listRulesWithType = currentListRules.map(rule => ({ ...rule, page_type: 'list' as const }))
      const detailRulesWithType = currentDetailRules.map(rule => ({ ...rule, page_type: 'detail' as const }))
      allRules = [...listRulesWithType, ...detailRulesWithType]
    } else if (callbackType === 'list') {
      allRules = currentListRules
    } else if (callbackType === 'detail') {
      allRules = currentDetailRules
    }
    
    return { 
      ...form.getFieldsValue(), 
      callback_type: callbackType,
      extraction_rules: JSON.stringify(allRules),
      pagination_config: JSON.stringify(paginationConfig)
    }
  }

  // 回调类型变化
  const handleCallbackTypeChange = (value: 'list' | 'detail' | 'mixed') => {
    setCallbackType(value)
    // 使用当前的规则值，因为callbackType状态可能还没有更新
    const updatedData = getUpdatedDataWithCallbackType(listRules, detailRules, value)
    onDataChange?.(updatedData)
  }

  // 分页配置变化
  const handlePaginationConfigChange = (config: any) => {
    setPaginationConfig(config)
    // 使用新的分页配置值
    const updatedData = getUpdatedDataWithPagination(config)
    onDataChange?.(updatedData)
  }

  // 获取更新后的数据（考虑新的回调类型）
  const getUpdatedDataWithCallbackType = (currentListRules: ExtractionRule[], currentDetailRules: ExtractionRule[], newCallbackType: 'list' | 'detail' | 'mixed') => {
    let allRules: ExtractionRule[] = []
    if (newCallbackType === 'mixed') {
      const listRulesWithType = currentListRules.map(rule => ({ ...rule, page_type: 'list' as const }))
      const detailRulesWithType = currentDetailRules.map(rule => ({ ...rule, page_type: 'detail' as const }))
      allRules = [...listRulesWithType, ...detailRulesWithType]
    } else if (newCallbackType === 'list') {
      allRules = currentListRules
    } else if (newCallbackType === 'detail') {
      allRules = currentDetailRules
    }
    
    return { 
      ...form.getFieldsValue(), 
      callback_type: newCallbackType,
      extraction_rules: JSON.stringify(allRules),
      pagination_config: JSON.stringify(paginationConfig)
    }
  }

  // 获取更新后的数据（考虑新的分页配置）
  const getUpdatedDataWithPagination = (newPaginationConfig: any) => {
    let allRules: ExtractionRule[] = []
    if (callbackType === 'mixed') {
      const listRulesWithType = listRules.map(rule => ({ ...rule, page_type: 'list' as const }))
      const detailRulesWithType = detailRules.map(rule => ({ ...rule, page_type: 'detail' as const }))
      allRules = [...listRulesWithType, ...detailRulesWithType]
    } else if (callbackType === 'list') {
      allRules = listRules
    } else if (callbackType === 'detail') {
      allRules = detailRules
    }
    
    return { 
      ...form.getFieldsValue(), 
      callback_type: callbackType,
      extraction_rules: JSON.stringify(allRules),
      pagination_config: JSON.stringify(newPaginationConfig),
      // v2.0.0 新增配置字段
      proxy_config: proxyConfig.enabled ? JSON.stringify(proxyConfig) : undefined,
      anti_spider: antiSpiderConfig.enabled ? JSON.stringify(antiSpiderConfig) : undefined,
      task_config: taskConfig.queue_priority || taskConfig.concurrency_limit !== 1 ? JSON.stringify(taskConfig) : undefined
    }
  }



  const tabItems = [
    {
      key: 'basic',
      label: '基本配置',
      children: (
        <Space direction="vertical" style={{ width: '100%' }} size="middle">
          {/* 1. 基本信息 */}
          <Card title="基本信息" size="small">
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
                  <Input placeholder="例如: 新闻,电商,数据采集" />
                </Form.Item>
              </Col>
            </Row>

            <Form.Item
              name="description"
              label="项目描述"
            >
              <TextArea
                rows={3}
                placeholder="请描述采集目标和用途"
                maxLength={500}
                showCount
              />
            </Form.Item>
          </Card>

          {/* 2. 采集配置 */}
          <Card title="采集配置" size="small">
            <Form.Item
              name="target_url"
              label="目标URL"
              rules={[
                { required: true, message: '请输入目标URL' },
                { type: 'url', message: '请输入有效的URL' }
              ]}
            >
              <Input placeholder="https://example.com" />
            </Form.Item>

            <Row gutter={16}>
              <Col span={12}>
                <Form.Item
                  name="url_pattern"
                  label="URL匹配模式"
                  tooltip="用于匹配需要采集的URL，支持正则表达式"
                >
                  <Input placeholder="例如: https://example.com/news/.*" />
                </Form.Item>
              </Col>
              <Col span={12}>
                <Form.Item
                  name="request_method"
                  label="请求方法"
                >
                  <Select>
                    <Option value="GET">GET</Option>
                    <Option value="POST">POST</Option>
                    <Option value="PUT">PUT</Option>
                    <Option value="DELETE">DELETE</Option>
                  </Select>
                </Form.Item>
              </Col>
            </Row>

            <Row gutter={16}>
              <Col span={8}>
                <Form.Item
                  name="request_delay"
                  label="请求延迟 (秒)"
                  tooltip="两次请求之间的时间间隔"
                >
                  <InputNumber
                    min={0}
                    max={60}
                    step={0.5}
                    style={{ width: '100%' }}
                    placeholder="请求间隔时间"
                  />
                </Form.Item>
              </Col>
              <Col span={8}>
                <Form.Item
                  name="retry_count"
                  label="重试次数"
                >
                  <InputNumber
                    min={0}
                    max={10}
                    style={{ width: '100%' }}
                  />
                </Form.Item>
              </Col>
              <Col span={8}>
                <Form.Item
                  name="timeout"
                  label="超时时间 (秒)"
                >
                  <InputNumber
                    min={5}
                    max={300}
                    style={{ width: '100%' }}
                  />
                </Form.Item>
              </Col>
            </Row>

            <Form.Item
              name="priority"
              label="优先级"
              tooltip="数值越大优先级越高"
            >
              <InputNumber
                min={-999}
                max={999}
                style={{ width: '50%' }}
                placeholder="任务优先级"
              />
            </Form.Item>
          </Card>

          {/* 3. 回调类型选择 */}
          <Card 
            title={
              <Space>
                <AppstoreOutlined />
                回调类型
              </Space>
            } 
            size="small"
          >
            <Form.Item
              label="选择回调类型"
              tooltip="根据您的需求选择合适的回调类型，不同类型需要配置不同的提取规则"
            >
              <Radio.Group
                value={callbackType}
                onChange={(e) => handleCallbackTypeChange(e.target.value)}
                optionType="button"
                buttonStyle="solid"
                size="middle"
              >
                <Tooltip 
                  title={
                    <div>
                      <div><strong>混合模式</strong></div>
                      <div>• 需要配置列表页和详情页的提取规则</div>
                      <div>• 适用于：新闻网站、电商产品、论坛帖子</div>
                      <div>• 数据流程：列表页 → 详情页 → 完整数据</div>
                      <div>• 规则需标记page_type字段</div>
                    </div>
                  }
                  placement="top"
                >
                  <Radio value="mixed">
                    <Space>
                      <AppstoreOutlined style={{ color: '#1890ff' }} />
                      混合模式
                    </Space>
                  </Radio>
                </Tooltip>
                
                <Tooltip 
                  title={
                    <div>
                      <div><strong>列表页模式</strong></div>
                      <div>• 只需配置列表页规则，无需进入详情页</div>
                      <div>• 适用于：文章列表、产品目录、搜索结果</div>
                      <div>• 数据流程：列表页 → 提取数据 → 完成</div>
                    </div>
                  }
                  placement="top"
                >
                  <Radio value="list">
                    <Space>
                      <UnorderedListOutlined style={{ color: '#52c41a' }} />
                      列表页
                    </Space>
                  </Radio>
                </Tooltip>
                
                <Tooltip 
                  title={
                    <div>
                      <div><strong>详情页模式</strong></div>
                      <div>• 只需配置详情页规则，直接采集单页数据</div>
                      <div>• 适用于：单篇文章、产品详情、个人主页</div>
                      <div>• 数据流程：直接访问 → 提取数据 → 完成</div>
                    </div>
                  }
                  placement="top"
                >
                  <Radio value="detail">
                    <Space>
                      <FileTextOutlined style={{ color: '#fa8c16' }} />
                      详情页
                    </Space>
                  </Radio>
                </Tooltip>
              </Radio.Group>
            </Form.Item>
          </Card>

          {/* 4. 选择采集引擎 */}
          <Card 
            title={
              <Space>
                <ThunderboltOutlined />
                选择采集引擎
              </Space>
            } 
            size="small"
          >
            <Form.Item
              name="engine"
              rules={[{ required: true, message: '请选择采集引擎' }]}
            >
              <div style={{ display: 'grid', gridTemplateColumns: 'repeat(3, 1fr)', gap: 16 }}>
                {CRAWL_ENGINES.map(engine => (
                  <Card
                    key={engine.value}
                    size="small"
                    hoverable
                    style={{
                      cursor: 'pointer',
                      borderColor: selectedEngine === engine.value ? '#1890ff' : undefined,
                      borderWidth: selectedEngine === engine.value ? 2 : 1
                    }}
                    onClick={() => {
                      setSelectedEngine(engine.value)
                      form.setFieldsValue({ engine: engine.value })
                    }}
                  >
                    <div style={{ textAlign: 'center' }}>
                      <div style={{ fontSize: 32, color: engine.color, marginBottom: 8 }}>
                        {engine.icon}
                      </div>
                      <Title level={5} style={{ margin: '8px 0' }}>
                        {engine.label}
                      </Title>
                      <Text type="secondary" style={{ fontSize: 12 }}>
                        {engine.description}
                      </Text>
                      <Divider style={{ margin: '12px 0' }} />
                      <div style={{ textAlign: 'left' }}>
                        <Text strong style={{ fontSize: 12 }}>特点：</Text>
                        <div style={{ marginTop: 4 }}>
                          {engine.features.map((feat, idx) => (
                            <Tag key={idx} color={engine.color} style={{ marginBottom: 4, fontSize: 11 }}>
                              {feat}
                            </Tag>
                          ))}
                        </div>
                        <Text strong style={{ fontSize: 12, display: 'block', marginTop: 8 }}>
                          适用场景：
                        </Text>
                        <Text type="secondary" style={{ fontSize: 11 }}>
                          {engine.scenarios.join('、')}
                        </Text>
                      </div>
                    </div>
                  </Card>
                ))}
              </div>
            </Form.Item>
          </Card>
        </Space>
      )
    },
    // 根据回调类型动态显示规则配置
    ...(callbackType === 'mixed' ? [
      {
        key: 'list_rules',
        label: '列表页规则',
        children: (
          <Card 
            title={
              <Space>
                <UnorderedListOutlined />
                列表页数据提取规则
              </Space>
            }
            size="small"
          >
            <Alert
              message="列表页规则配置"
              description="配置从列表页面提取数据的规则，通常包括：文章链接、标题、摘要、作者、时间等"
              type="info"
              showIcon
              style={{ marginBottom: 16 }}
            />
            <RuleSelector
              rules={listRules}
              onChange={handleListRulesChange}
              placeholder="添加列表页提取规则"
              required
              showPageType={false}
              defaultPageType="list"
            />
          </Card>
        )
      },
      {
        key: 'detail_rules',
        label: '详情页规则',
        children: (
          <Card 
            title={
              <Space>
                <FileTextOutlined />
                详情页数据提取规则
              </Space>
            }
            size="small"
          >
            <Alert
              message="详情页规则配置"
              description="配置从详情页面提取数据的规则，通常包括：正文内容、作者、发布时间、来源等"
              type="info"
              showIcon
              style={{ marginBottom: 16 }}
            />
            <RuleSelector
              rules={detailRules}
              onChange={handleDetailRulesChange}
              placeholder="添加详情页提取规则"
              required
              showPageType={false}
              defaultPageType="detail"
            />
          </Card>
        )
      }
    ] : callbackType === 'list' ? [
      {
        key: 'list_rules',
        label: '列表页规则',
        children: (
          <Card 
            title={
              <Space>
                <UnorderedListOutlined />
                列表页数据提取规则
              </Space>
            }
            size="small"
          >
            <Alert
              message="列表页模式"
              description="只需配置列表页的提取规则，适用于只需要列表数据的场景"
              type="success"
              showIcon
              style={{ marginBottom: 16 }}
            />
            <RuleSelector
              rules={listRules}
              onChange={handleListRulesChange}
              placeholder="添加列表页提取规则"
              required
              showPageType={false}
              defaultPageType="list"
            />
          </Card>
        )
      }
    ] : [
      {
        key: 'detail_rules',
        label: '详情页规则',
        children: (
          <Card 
            title={
              <Space>
                <FileTextOutlined />
                详情页数据提取规则
              </Space>
            }
            size="small"
          >
            <Alert
              message="详情页模式"
              description="只需配置详情页的提取规则，适用于采集单个页面数据的场景"
              type="success"
              showIcon
              style={{ marginBottom: 16 }}
            />
            <RuleSelector
              rules={detailRules}
              onChange={handleDetailRulesChange}
              placeholder="添加详情页提取规则"
              required
              showPageType={false}
              defaultPageType="detail"
            />
          </Card>
        )
      }
    ]),
    {
      key: 'pagination',
      label: '翻页配置',
      children: (
        <Card 
          title="翻页配置" 
          size="small"
        >
          <Row gutter={16}>
            <Col span={8}>
              <Form.Item
                label="翻页类型"
                tooltip="选择分页处理方式"
              >
                <Select
                  value={paginationConfig.method}
                  onChange={(value) => handlePaginationConfigChange({
                    ...paginationConfig,
                    method: value
                  })}
                >
                  <Option value="none">无分页</Option>
                  <Option value="url_param">URL参数翻页</Option>
                  <Option value="javascript">JS点击翻页</Option>
                  <Option value="ajax">AJAX加载</Option>
                  <Option value="infinite_scroll">无限滚动</Option>
                </Select>
              </Form.Item>
            </Col>
            <Col span={8}>
              <Form.Item
                label="起始页码"
                tooltip="从第几页开始采集"
              >
                <InputNumber
                  min={0}
                  max={100}
                  style={{ width: '100%' }}
                  value={paginationConfig.start_page}
                  onChange={(value) => handlePaginationConfigChange({
                    ...paginationConfig,
                    start_page: value || 1
                  })}
                />
              </Form.Item>
            </Col>
            <Col span={8}>
              <Form.Item
                label="最大页数"
                tooltip="限制采集的最大页数"
              >
                <InputNumber
                  min={1}
                  max={1000}
                  style={{ width: '100%' }}
                  value={paginationConfig.max_pages}
                  onChange={(value) => handlePaginationConfigChange({
                    ...paginationConfig,
                    max_pages: value || 10
                  })}
                />
              </Form.Item>
            </Col>
          </Row>

          {/* 根据翻页类型显示不同的配置 */}
          {paginationConfig.method === 'url_param' && (
            <Form.Item
              label="URL模板"
              tooltip="使用{page}作为页码占位符"
            >
              <Input
                placeholder="例如: /list/page/{page} 或 ?page={page}"
                value={paginationConfig.url_template}
                onChange={(e) => handlePaginationConfigChange({
                  ...paginationConfig,
                  url_template: e.target.value
                })}
              />
            </Form.Item>
          )}

          {(paginationConfig.method === 'javascript' || paginationConfig.method === 'ajax') && (
            <>
              <Alert
                message="下一页规则"
                description="配置如何找到并点击下一页按钮"
                type="info"
                style={{ marginBottom: 16 }}
              />
              <RuleSelector
                rules={paginationConfig.next_page_rule ? [paginationConfig.next_page_rule] : []}
                onChange={(rules) => handlePaginationConfigChange({
                  ...paginationConfig,
                  next_page_rule: rules[0] || undefined
                })}
                placeholder="配置下一页选择器"
              />
              <Form.Item
                label="点击后等待时间 (毫秒)"
                style={{ marginTop: 16 }}
              >
                <InputNumber
                  min={0}
                  max={10000}
                  step={500}
                  style={{ width: '100%' }}
                  value={paginationConfig.wait_after_click_ms}
                  onChange={(value) => handlePaginationConfigChange({
                    ...paginationConfig,
                    wait_after_click_ms: value || 2000
                  })}
                />
              </Form.Item>
            </>
          )}
        </Card>
      )
    },
    {
      key: 'advanced',
      label: '高级配置',
      children: (
        <Space direction="vertical" style={{ width: '100%' }} size="middle">
          {/* HTTP配置 */}
          <Card title="HTTP配置" size="small">
            <Form.Item
              name="headers"
              label="请求头"
              tooltip='JSON格式，例如: {"User-Agent": "MyBot"}'
            >
              <TextArea
                rows={3}
                placeholder='{"User-Agent": "Mozilla/5.0...", "Accept": "text/html,application/xhtml+xml..."}'
              />
            </Form.Item>

            <Form.Item
              name="cookies"
              label="Cookies"
              tooltip='JSON格式，例如: {"session_id": "abc123"}'
            >
              <TextArea
                rows={2}
                placeholder='{"session_id": "abc123", "auth_token": "xyz789"}'
              />
            </Form.Item>
          </Card>

          {/* 反爬虫配置 */}
          <Card 
            title={
              <Space>
                <BugOutlined />
                反爬虫配置
              </Space>
            } 
            size="small"
          >
            <Alert
              message="反爬虫策略"
              description={`当前引擎：${selectedEngine === 'curl_cffi' ? '已选择 Curl CFFI 引擎，自带强大的反检测能力' : '可切换到 Curl CFFI 引擎获得更好的反爬虫能力'}`}
              type={selectedEngine === 'curl_cffi' ? 'success' : 'info'}
              showIcon
              style={{ marginBottom: 16 }}
            />
            
            <Form.Item
              label="启用反爬虫"
              style={{ marginBottom: 16 }}
            >
              <Radio.Group
                value={antiSpiderConfig.enabled}
                onChange={(e) => setAntiSpiderConfig({ ...antiSpiderConfig, enabled: e.target.value })}
              >
                <Radio value={false}>关闭</Radio>
                <Radio value={true}>开启</Radio>
              </Radio.Group>
            </Form.Item>
            
            {antiSpiderConfig.enabled && (
              <Row gutter={16} style={{ marginBottom: 16 }}>
                <Col span={8}>
                  <div style={{ marginBottom: 16 }}>
                    <div style={{ marginBottom: 8, fontSize: 14 }}>User-Agent轮换</div>
                    <Radio.Group
                      value={antiSpiderConfig.user_agent_rotation}
                      onChange={(e) => setAntiSpiderConfig({ ...antiSpiderConfig, user_agent_rotation: e.target.value })}
                    >
                      <Radio value={false}>关闭</Radio>
                      <Radio value={true}>开启</Radio>
                    </Radio.Group>
                  </div>
                </Col>
                <Col span={8}>
                  <div style={{ marginBottom: 16 }}>
                    <div style={{ marginBottom: 8, fontSize: 14 }}>随机延迟</div>
                    <Radio.Group
                      value={antiSpiderConfig.random_delay}
                      onChange={(e) => setAntiSpiderConfig({ ...antiSpiderConfig, random_delay: e.target.value })}
                    >
                      <Radio value={false}>关闭</Radio>
                      <Radio value={true}>开启</Radio>
                    </Radio.Group>
                  </div>
                </Col>
                <Col span={8}>
                  <div style={{ marginBottom: 16 }}>
                    <div style={{ marginBottom: 8, fontSize: 14 }}>Cookie持久化</div>
                    <Radio.Group
                      value={antiSpiderConfig.cookie_persistence}
                      onChange={(e) => setAntiSpiderConfig({ ...antiSpiderConfig, cookie_persistence: e.target.value })}
                    >
                      <Radio value={false}>关闭</Radio>
                      <Radio value={true}>开启</Radio>
                    </Radio.Group>
                  </div>
                </Col>
              </Row>
            )}
            
            <Row gutter={16}>
              <Col span={12}>
                <Form.Item
                  name="dont_filter"
                  label="禁用去重"
                  valuePropName="checked"
                  tooltip="是否禁用URL去重功能"
                >
                  <Select>
                    <Option value={false}>启用去重</Option>
                    <Option value={true}>禁用去重</Option>
                  </Select>
                </Form.Item>
              </Col>
              <Col span={12}>
                <Form.Item
                  label="超时时间(秒)"
                  name="timeout"
                  tooltip="请求超时时间"
                >
                  <InputNumber
                    min={1}
                    max={300}
                    placeholder="30"
                    style={{ width: '100%' }}
                  />
                </Form.Item>
              </Col>
            </Row>
          </Card>

          {/* 代理配置 */}
          <Card 
            title={
              <Space>
                <SafetyOutlined />
                代理配置 (v2.0)
              </Space>
            } 
            size="small"
          >
            <Form.Item
              label="启用代理"
              style={{ marginBottom: 16 }}
            >
              <Radio.Group
                value={proxyConfig.enabled}
                onChange={(e) => setProxyConfig({ ...proxyConfig, enabled: e.target.value })}
              >
                <Radio value={false}>关闭</Radio>
                <Radio value={true}>开启</Radio>
              </Radio.Group>
            </Form.Item>
            
            {proxyConfig.enabled && (
              <>
                <Row gutter={16}>
                  <Col span={12}>
                    <div style={{ marginBottom: 16 }}>
                      <div style={{ marginBottom: 8, fontSize: 14 }}>代理类型</div>
                      <Select
                        value={proxyConfig.proxy_type}
                        onChange={(value) => setProxyConfig({ ...proxyConfig, proxy_type: value })}
                      >
                        <Option value="http">HTTP</Option>
                        <Option value="https">HTTPS</Option>
                        <Option value="socks4">SOCKS4</Option>
                        <Option value="socks5">SOCKS5</Option>
                      </Select>
                    </div>
                  </Col>
                  <Col span={12}>
                    <div style={{ marginBottom: 16 }}>
                      <div style={{ marginBottom: 8, fontSize: 14 }}>代理地址</div>
                      <Input
                        value={proxyConfig.proxy_url}
                        onChange={(e) => setProxyConfig({ ...proxyConfig, proxy_url: e.target.value })}
                        placeholder="http://proxy.example.com:8080"
                      />
                    </div>
                  </Col>
                </Row>
                <Row gutter={16}>
                  <Col span={12}>
                    <div style={{ marginBottom: 16 }}>
                      <div style={{ marginBottom: 8, fontSize: 14 }}>用户名（可选）</div>
                      <Input
                        value={proxyConfig.username}
                        onChange={(e) => setProxyConfig({ ...proxyConfig, username: e.target.value })}
                        placeholder="代理用户名"
                      />
                    </div>
                  </Col>
                  <Col span={12}>
                    <div style={{ marginBottom: 16 }}>
                      <div style={{ marginBottom: 8, fontSize: 14 }}>密码（可选）</div>
                      <Input.Password
                        value={proxyConfig.password}
                        onChange={(e) => setProxyConfig({ ...proxyConfig, password: e.target.value })}
                        placeholder="代理密码"
                      />
                    </div>
                  </Col>
                </Row>
              </>
            )}
          </Card>

          {/* 任务配置 */}
          <Card 
            title={
              <Space>
                <SettingOutlined />
                任务配置 (v2.0)
              </Space>
            } 
            size="small"
          >
            <Row gutter={16}>
              <Col span={12}>
                <div style={{ marginBottom: 16 }}>
                  <div style={{ marginBottom: 8, fontSize: 14 }}>队列优先级</div>
                  <InputNumber
                    min={0}
                    max={10}
                    value={taskConfig.queue_priority}
                    onChange={(value) => setTaskConfig({ ...taskConfig, queue_priority: value || 0 })}
                    style={{ width: '100%' }}
                  />
                </div>
              </Col>
              <Col span={12}>
                <div style={{ marginBottom: 16 }}>
                  <div style={{ marginBottom: 8, fontSize: 14 }}>并发限制</div>
                  <InputNumber
                    min={1}
                    max={10}
                    value={taskConfig.concurrency_limit}
                    onChange={(value) => setTaskConfig({ ...taskConfig, concurrency_limit: value || 1 })}
                    style={{ width: '100%' }}
                  />
                </div>
              </Col>
            </Row>
            
            <div style={{ marginBottom: 16 }}>
              <div style={{ marginBottom: 8, fontSize: 14 }}>任务ID模板</div>
              <Input
                value={taskConfig.task_id_template}
                onChange={(e) => setTaskConfig({ ...taskConfig, task_id_template: e.target.value })}
                placeholder="例如: news_{timestamp}_{page}"
              />
            </div>
            
            <div style={{ marginBottom: 16 }}>
              <div style={{ marginBottom: 8, fontSize: 14 }}>工作节点ID</div>
              <Input
                value={taskConfig.worker_id}
                onChange={(e) => setTaskConfig({ ...taskConfig, worker_id: e.target.value })}
                placeholder="例如: worker-001"
              />
            </div>
          </Card>
        </Space>
      )
    }
  ]

  return (
    <div>
      <div style={{ textAlign: 'center', marginBottom: 24 }}>
        <Title level={4}>
          <SettingOutlined style={getIconStyle()} />
          规则项目配置
        </Title>
        <Text type="secondary">
          配置网页数据采集规则，支持多种采集引擎和灵活的规则配置
        </Text>
      </div>

      <Form
        form={form}
        layout="vertical"
        initialValues={{
          ...initialData,
          engine: initialData.engine || 'requests',
          request_method: initialData.request_method || 'GET',
          request_delay: initialData.request_delay !== undefined ? initialData.request_delay : 1,
          retry_count: initialData.retry_count !== undefined ? initialData.retry_count : 3,
          timeout: initialData.timeout !== undefined ? initialData.timeout : 30,
          priority: initialData.priority !== undefined ? initialData.priority : 0,
          // request_delay已经在ProjectEditDrawer中转换为秒，这里无需再次转换
          // 确保headers和cookies显示为字符串格式
          headers: (() => {
            if (!initialData.headers) return ''
            if (typeof initialData.headers === 'string') return initialData.headers
            return JSON.stringify(initialData.headers, null, 2)
          })(),
          cookies: (() => {
            if (!initialData.cookies) return ''
            if (typeof initialData.cookies === 'string') return initialData.cookies
            return JSON.stringify(initialData.cookies, null, 2)
          })()
        }}
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
export default React.memo(RuleProjectForm)