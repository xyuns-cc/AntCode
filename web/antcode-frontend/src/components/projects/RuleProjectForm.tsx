import React, { useCallback, useState } from 'react'
import {
  Form,
  Input,
  Space,
  Typography,
  Card,
  Select,
  InputNumber,
  Tabs,
  Row,
  Col,
  Divider,
  Tag,
  Radio
} from 'antd'
import { RocketOutlined, SafetyOutlined, ThunderboltOutlined, BugOutlined, UnorderedListOutlined, FileTextOutlined } from '@ant-design/icons'
import { useThemeContext } from '@/contexts/ThemeContext'
import RuleSelector from './RuleSelector'
import type { ProjectCreateRequest, ExtractionRule, ProxyConfig, AntiSpiderConfig } from '@/types'

// 本地分页配置类型 — S6/S7/S8 扩展
interface FormPaginationConfig {
  method:
    | 'none'
    | 'url_pattern'
    | 'url_param'
    | 'click_element'
    | 'js_click'
    | 'infinite_scroll'
    | 'javascript'  // 旧值兼容，运行时映射到 infinite_scroll
    | 'ajax'        // 旧值兼容
  start_page?: number
  max_pages?: number
  // next 选择器：字符串（自动识别 CSS/XPath）或结构化 {type, expr}
  next_page_rule?: ExtractionRule | { type: 'css' | 'xpath' | 'text'; expr: string } | string
  wait_after_click_ms?: number
  url_template?: string
  page_param?: string
  scroll_count?: number
  scroll_wait_ms?: number
}

// S5: 去重配置
interface FormDedupConfig {
  enabled?: boolean
  fields?: string[]
  scope?: 'project' | 'run'
  ttl_days?: number
  on_hit?: 'drop' | 'log'
}

// next_page_rule 结构化 selector 类型
type NextSelectorType = 'css' | 'xpath' | 'text'
interface NextSelectorObject {
  type: NextSelectorType
  expr: string
}
function normalizeNextRule(raw: FormPaginationConfig['next_page_rule']): NextSelectorObject {
  if (!raw) return { type: 'css', expr: '' }
  if (typeof raw === 'string') {
    const trimmed = raw.trim()
    if (trimmed.startsWith('//') || trimmed.startsWith('..') || trimmed.startsWith('xpath=')) {
      return { type: 'xpath', expr: trimmed.replace(/^xpath=/, '') }
    }
    return { type: 'css', expr: trimmed }
  }
  // ExtractionRule 兼容：desc/type/expr
  if ('type' in raw && 'expr' in raw) {
    const t = raw.type
    if (t === 'css' || t === 'xpath') return { type: t, expr: String(raw.expr || '') }
    // regex/jsonpath 等旧字段回退到 css
    return { type: 'css', expr: String(raw.expr || '') }
  }
  return { type: 'css', expr: '' }
}

const { Title, Text } = Typography
const { TextArea } = Input
const { Option } = Select

const parseConfigValue = <T extends object>(value: string | T | undefined, emptyValue: T, fieldName: string): T => {
  if (!value) {
    return emptyValue
  }
  if (typeof value === 'string') {
    try {
      return JSON.parse(value) as T
    } catch (error) {
      throw new Error(`${fieldName} JSON 解析失败`, { cause: error })
    }
  }
  return value
}

// 表单初始数据类型（tags 可以是字符串或数组，headers/cookies 支持字符串或对象格式）
interface RuleProjectFormInitialData extends Omit<Partial<ProjectCreateRequest>, 'tags' | 'headers' | 'cookies' | 'callback_type' | 'extraction_rules' | 'proxy_config' | 'anti_spider'> {
  tags?: string | string[]
  headers?: string | Record<string, string>
  cookies?: string | Record<string, string>
  callback_type?: string  // 灵活类型，接受后端返回的任意值
  extraction_rules?: string | ExtractionRule[] | Record<string, unknown>  // 支持字符串、数组或对象格式
  proxy_config?: string | ProxyConfig
  anti_spider?: string | AntiSpiderConfig
}

interface RuleProjectFormProps {
  initialData?: RuleProjectFormInitialData
  onDataChange?: (data: Partial<ProjectCreateRequest>) => void
  onSubmit: (data: Record<string, unknown>) => void
  loading?: boolean
  isEdit?: boolean
  onValidationChange?: (isValid: boolean, tooltip: string) => void
  onRef?: (ref: { submit: () => void }) => void
}

// 采集引擎配置（S1: 新增 Playwright，对齐后端 CrawlEngine 枚举）
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
    value: 'curl_cffi',
    label: 'Curl CFFI (反检测)',
    icon: <SafetyOutlined />,
    color: 'orange',
    description: '强大的反爬虫能力',
    features: ['真实 curl-cffi 请求栈', '反检测能力强', 'TLS指纹伪装'],
    scenarios: ['电商网站', '有反爬虫的网站', '需要绕过CF的网站']
  },
  {
    value: 'playwright',
    label: 'Playwright (浏览器渲染)',
    icon: <RocketOutlined />,
    color: 'purple',
    description: 'JS 渲染 + 交互能力',
    features: ['真实 Chromium', 'SPA/懒加载友好', '支持 JS 点击/滚动翻页'],
    scenarios: ['SPA 应用', 'JS 动态渲染', 'AJAX 加载列表']
  }
]

const RuleProjectForm: React.FC<RuleProjectFormProps> = ({
  initialData = {},
  onDataChange,
  onSubmit,
  loading: _loading = false,
  isEdit: _isEdit = false,
  onValidationChange,
  onRef
}) => {
  const initialEngine = (
    initialData.engine === 'curl_cffi' ||
    initialData.engine === 'playwright' ||
    initialData.engine === 'requests'
  )
    ? initialData.engine
    : 'requests'
  const [form] = Form.useForm()
  useThemeContext() // 保持主题上下文订阅
  const [listRules, setListRules] = useState<ExtractionRule[]>([])
  const [detailRules, setDetailRules] = useState<ExtractionRule[]>([])
  const [paginationConfig, setPaginationConfig] = useState<FormPaginationConfig>(() => {
    if (!initialData.pagination_config) {
      return { method: 'none', max_pages: 10, start_page: 1 }
    }
    try {
      return JSON.parse(initialData.pagination_config)
    } catch (error) {
      throw new Error('pagination_config JSON 解析失败', { cause: error })
    }
  })
  const [selectedEngine, setSelectedEngine] = useState(initialEngine)
  const [callbackType, setCallbackType] = useState<'list' | 'detail' | 'mixed'>('mixed')
  
  // v2.0.0 新增状态 - 安全解析JSON
  const [proxyConfig, setProxyConfig] = useState<ProxyConfig>(() =>
    parseConfigValue(initialData.proxy_config, { enabled: false, proxy_type: 'http' }, 'proxy_config')
  )
  
  const [antiSpiderConfig, setAntiSpiderConfig] = useState<AntiSpiderConfig>(() =>
    parseConfigValue(initialData.anti_spider, {
      enabled: false,
      user_agent_rotation: false,
      random_delay: false
    }, 'anti_spider')
  )

  // S5: 内容去重配置
  const [dedupConfig, setDedupConfig] = useState<FormDedupConfig>(() =>
    parseConfigValue(
      (initialData as Record<string, unknown>).dedup_config as string | FormDedupConfig | undefined,
      { enabled: false, fields: [], scope: 'project', ttl_days: 30, on_hit: 'drop' } as FormDedupConfig,
      'dedup_config'
    )
  )
  // S3b: scrapy-redis 断点续爬开关
  const [resumeEnabled, setResumeEnabled] = useState<boolean>(
    Boolean((initialData as Record<string, unknown>).resume_enabled)
  )
  
  // 获取按钮禁用状态
  const getButtonDisabled = useCallback(() => {
    if (callbackType === 'mixed') {
      return listRules.length === 0 || detailRules.length === 0
    } else if (callbackType === 'list') {
      return listRules.length === 0
    } else if (callbackType === 'detail') {
      return detailRules.length === 0
    }
    return true
  }, [callbackType, detailRules.length, listRules.length])

  // 获取按钮提示文本
  const getButtonTooltip = useCallback(() => {
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
  }, [callbackType, detailRules.length, listRules.length])

  // 初始化callbackType
  React.useEffect(() => {
    if (initialData.callback_type && ['list', 'detail', 'mixed'].includes(initialData.callback_type)) {
      setCallbackType(initialData.callback_type as 'list' | 'detail' | 'mixed')
    }
  }, [initialData.callback_type])

  // 初始化规则
  React.useEffect(() => {
    if (initialData.extraction_rules) {
      try {
        // 支持字符串、数组或对象格式
        let rules: ExtractionRule[] = []
        if (typeof initialData.extraction_rules === 'string') {
          rules = JSON.parse(initialData.extraction_rules)
        } else if (Array.isArray(initialData.extraction_rules)) {
          rules = initialData.extraction_rules
        }
        
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
      } catch (error) {
        throw new Error('extraction_rules JSON 解析失败', { cause: error })
      }
    }
  }, [initialData.extraction_rules, callbackType])

  // 通知父组件验证状态变化
  // P1-31: 依赖里去掉 onValidationChange/getButtonDisabled/getButtonTooltip 三个
  // 函数引用 —— 它们每次渲染都是新引用,会导致 effect 死循环。用 ref 承接
  // onValidationChange,并只在**序列化后的验证状态**变化时重触发。
  const onValidationChangeRef = React.useRef(onValidationChange)
  React.useEffect(() => {
    onValidationChangeRef.current = onValidationChange
  }, [onValidationChange])

  React.useEffect(() => {
    const isValid = !getButtonDisabled()
    const tooltip = getButtonTooltip()
    onValidationChangeRef.current?.(isValid, tooltip)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [listRules, detailRules, callbackType])

  // 提供submit方法给父组件
  React.useEffect(() => {
    onRef?.({
      submit: () => {
        form.submit()
      }
    })
  }, [form, onRef])

  const buildSubmitData = useCallback(
    (
      values: ProjectCreateRequest,
      overrides?: {
        callbackType?: 'list' | 'detail' | 'mixed'
        listRules?: ExtractionRule[]
        detailRules?: ExtractionRule[]
        paginationConfig?: FormPaginationConfig
      }
    ) => {
      const currentCallbackType = overrides?.callbackType ?? callbackType
      const currentListRules = overrides?.listRules ?? listRules
      const currentDetailRules = overrides?.detailRules ?? detailRules
      const currentPaginationConfig = overrides?.paginationConfig ?? paginationConfig

      let allRules: ExtractionRule[] = []
      if (currentCallbackType === 'mixed') {
        const listRulesWithType = currentListRules.map(rule => ({ ...rule, page_type: 'list' as const }))
        const detailRulesWithType = currentDetailRules.map(rule => ({ ...rule, page_type: 'detail' as const }))
        allRules = [...listRulesWithType, ...detailRulesWithType]
      } else if (currentCallbackType === 'list') {
        allRules = currentListRules
      } else if (currentCallbackType === 'detail') {
        allRules = currentDetailRules
      }

      const shouldPersistProxyConfig = proxyConfig.enabled || Boolean(initialData.proxy_config)
      const shouldPersistAntiSpider = antiSpiderConfig.enabled || Boolean(initialData.anti_spider)
      const shouldPersistDedup = dedupConfig.enabled ||
        Boolean((initialData as Record<string, unknown>).dedup_config)

      return {
        ...values,
        callback_type: currentCallbackType,
        extraction_rules: JSON.stringify(allRules),
        pagination_config: JSON.stringify(currentPaginationConfig),
        proxy_config: shouldPersistProxyConfig ? JSON.stringify(proxyConfig) : undefined,
        anti_spider: shouldPersistAntiSpider ? JSON.stringify(antiSpiderConfig) : undefined,
        // S5 / S3b：只在实际启用时下发
        dedup_config: shouldPersistDedup ? JSON.stringify(dedupConfig) : undefined,
        resume_enabled: resumeEnabled ? true : undefined,
      }
    },
    [
      antiSpiderConfig,
      callbackType,
      detailRules,
      initialData,
      listRules,
      paginationConfig,
      proxyConfig,
      dedupConfig,
      resumeEnabled,
    ]
  )

  // P1-31: 稳定 onDataChange 引用,依赖里只放真实状态数据(不放 buildSubmitData / form / onDataChange
  // 三个每次都新的引用),避免父组件回调无 memo 时死循环。
  const onDataChangeRef = React.useRef(onDataChange)
  React.useEffect(() => {
    onDataChangeRef.current = onDataChange
  }, [onDataChange])

  React.useEffect(() => {
    if (!onDataChangeRef.current) {
      return
    }
    onDataChangeRef.current(buildSubmitData(form.getFieldsValue()))
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [antiSpiderConfig, proxyConfig, dedupConfig, resumeEnabled])

  // 表单提交
  const handleFinish = (values: ProjectCreateRequest) => {
    // 处理headers和cookies，根据API文档，统一API支持对象和字符串两种格式
    let processedHeaders = values.headers
    let processedCookies = values.cookies
    
    // 如果是字符串，尝试解析为对象（符合API文档要求）
    if (processedHeaders && typeof processedHeaders === 'string') {
      try {
        processedHeaders = JSON.parse(processedHeaders)
      } catch (error) {
        throw new Error('headers JSON 解析失败', { cause: error })
      }
    }
    
    if (processedCookies && typeof processedCookies === 'string') {
      try {
        processedCookies = JSON.parse(processedCookies)
      } catch (error) {
        throw new Error('cookies JSON 解析失败', { cause: error })
      }
    }

    const submitData = buildSubmitData(values)
    const allRules = typeof submitData.extraction_rules === 'string'
      ? JSON.parse(submitData.extraction_rules) as ExtractionRule[]
      : []

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

    const finalSubmitData: Record<string, unknown> = {
      ...submitData,
      type: 'rule',
      extraction_rules: JSON.stringify(cleanedRules), // API文档要求JSON字符串格式
      headers: processedHeaders, // 支持对象和字符串两种格式
      cookies: processedCookies, // 支持对象和字符串两种格式
      tags: Array.isArray(values.tags) 
        ? values.tags 
        : (typeof values.tags === 'string' 
          ? (values.tags as string).split(',').map((tag: string) => tag.trim()).filter(Boolean) 
          : []),
      request_delay: values.request_delay !== undefined ? Math.round(values.request_delay * 1000) : 1000, // 转换为毫秒整数
    }
    
    onSubmit(finalSubmitData)
  }

  // 表单值变化
  const handleValuesChange = (
    _changedValues: Partial<ProjectCreateRequest>,
    allValues: ProjectCreateRequest
  ) => {
    const updatedData = buildSubmitData(allValues)
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
    return buildSubmitData(form.getFieldsValue(), {
      listRules: currentListRules,
      detailRules: currentDetailRules
    })
  }

  // 回调类型变化
  const handleCallbackTypeChange = (value: 'list' | 'detail' | 'mixed') => {
    setCallbackType(value)
    // 使用当前的规则值，因为callbackType状态可能还没有更新
    const updatedData = getUpdatedDataWithCallbackType(listRules, detailRules, value)
    onDataChange?.(updatedData)
  }

  // 分页配置变化
  const handlePaginationConfigChange = (config: FormPaginationConfig) => {
    setPaginationConfig(config)
    // 使用新的分页配置值
    const updatedData = getUpdatedDataWithPagination(config)
    onDataChange?.(updatedData)
  }

  // 获取更新后的数据（考虑新的回调类型）
  const getUpdatedDataWithCallbackType = (currentListRules: ExtractionRule[], currentDetailRules: ExtractionRule[], newCallbackType: 'list' | 'detail' | 'mixed') => {
    return buildSubmitData(form.getFieldsValue(), {
      callbackType: newCallbackType,
      listRules: currentListRules,
      detailRules: currentDetailRules
    })
  }

  // 获取更新后的数据（考虑新的分页配置）
  const getUpdatedDataWithPagination = (newPaginationConfig: FormPaginationConfig) => {
    return buildSubmitData(form.getFieldsValue(), {
      paginationConfig: newPaginationConfig
    })
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

          {/* 3. 选择采集引擎 */}
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
              <div style={{ display: 'grid', gridTemplateColumns: 'repeat(2, 1fr)', gap: 16 }}>
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
    {
      key: 'rules',
      label: '提取规则',
      children: (
        <Space direction="vertical" style={{ width: '100%' }} size="middle">
          {/* 回调类型选择 - 紧凑卡片样式 */}
          <Card size="small" bodyStyle={{ padding: '12px 16px' }}>
            <Space size={12} align="center">
              <Text style={{ whiteSpace: 'nowrap' }}>采集模式:</Text>
              {/* 列表页 */}
              <div
                style={{
                  padding: '6px 16px',
                  border: `1px solid ${callbackType === 'list' ? '#1890ff' : '#d9d9d9'}`,
                  borderRadius: 6,
                  cursor: 'pointer',
                  background: callbackType === 'list' ? '#e6f7ff' : undefined,
                  display: 'flex',
                  alignItems: 'center',
                  gap: 6
                }}
                onClick={() => handleCallbackTypeChange('list')}
              >
                <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke={callbackType === 'list' ? '#1890ff' : '#999'} strokeWidth="2" style={{ display: 'block' }}>
                  <line x1="8" y1="6" x2="21" y2="6" />
                  <line x1="8" y1="12" x2="21" y2="12" />
                  <line x1="8" y1="18" x2="21" y2="18" />
                  <circle cx="4" cy="6" r="1" fill={callbackType === 'list' ? '#1890ff' : '#999'} />
                  <circle cx="4" cy="12" r="1" fill={callbackType === 'list' ? '#1890ff' : '#999'} />
                  <circle cx="4" cy="18" r="1" fill={callbackType === 'list' ? '#1890ff' : '#999'} />
                </svg>
                <span style={{ color: callbackType === 'list' ? '#1890ff' : undefined }}>列表页</span>
              </div>
              {/* 详情页 */}
              <div
                style={{
                  padding: '6px 16px',
                  border: `1px solid ${callbackType === 'detail' ? '#52c41a' : '#d9d9d9'}`,
                  borderRadius: 6,
                  cursor: 'pointer',
                  background: callbackType === 'detail' ? '#f6ffed' : undefined,
                  display: 'flex',
                  alignItems: 'center',
                  gap: 6
                }}
                onClick={() => handleCallbackTypeChange('detail')}
              >
                <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke={callbackType === 'detail' ? '#52c41a' : '#999'} strokeWidth="2" style={{ display: 'block' }}>
                  <rect x="3" y="3" width="18" height="18" rx="2" />
                  <line x1="7" y1="8" x2="17" y2="8" />
                  <line x1="7" y1="12" x2="17" y2="12" />
                  <line x1="7" y1="16" x2="13" y2="16" />
                </svg>
                <span style={{ color: callbackType === 'detail' ? '#52c41a' : undefined }}>详情页</span>
              </div>
              {/* 混合模式 */}
              <div
                style={{
                  padding: '6px 16px',
                  border: `1px solid ${callbackType === 'mixed' ? '#fa8c16' : '#d9d9d9'}`,
                  borderRadius: 6,
                  cursor: 'pointer',
                  background: callbackType === 'mixed' ? '#fff7e6' : undefined,
                  display: 'flex',
                  alignItems: 'center',
                  gap: 6
                }}
                onClick={() => handleCallbackTypeChange('mixed')}
              >
                <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke={callbackType === 'mixed' ? '#fa8c16' : '#999'} strokeWidth="2" style={{ display: 'block' }}>
                  <rect x="2" y="3" width="8" height="6" rx="1" />
                  <rect x="2" y="11" width="8" height="6" rx="1" />
                  <path d="M14 6h7M14 10h7M14 14h5" />
                  <circle cx="18" cy="18" r="3" />
                </svg>
                <span style={{ color: callbackType === 'mixed' ? '#fa8c16' : undefined }}>混合模式</span>
              </div>
            </Space>
          </Card>

          {/* 列表页规则 */}
          {(callbackType === 'mixed' || callbackType === 'list') && (
            <Card 
              title={<><UnorderedListOutlined /> 列表页提取规则</>}
              size="small"
            >
              <Form.Item
                label="列表项选择器"
                tooltip="用于定位列表中每个条目的容器元素"
                style={{ marginBottom: 16 }}
              >
                <Input 
                  placeholder="如: .list-item, ul > li, .article-list .item"
                  prefix={<UnorderedListOutlined style={{ color: '#999' }} />}
                />
              </Form.Item>
              <Divider style={{ margin: '12px 0' }} />
              <RuleSelector
                rules={listRules}
                onChange={handleListRulesChange}
                placeholder="添加列表页字段提取规则"
                required
                showPageType={false}
                defaultPageType="list"
              />
            </Card>
          )}

          {/* 详情页规则 */}
          {(callbackType === 'mixed' || callbackType === 'detail') && (
            <Card 
              title={<><FileTextOutlined /> 详情页提取规则</>}
              size="small"
            >
              <RuleSelector
                rules={detailRules}
                onChange={handleDetailRulesChange}
                placeholder="添加详情页提取规则"
                required
                showPageType={false}
                defaultPageType="detail"
              />
            </Card>
          )}

          {/* 翻页配置 — S6/S7/S8 */}
          <Card
            title="翻页配置"
            size="small"
            extra={
              <Text type="secondary" style={{ fontSize: 12 }}>
                提示：目标 URL 里可用 <code>{'{N}'}</code>（N=起始页号）、<code>{'{page}'}</code>、<code>{'{}'}</code> 占位符
              </Text>
            }
          >
            <Row gutter={16}>
              <Col span={8}>
                <Form.Item label="翻页方式" style={{ marginBottom: 12 }}>
                  <Select
                    value={paginationConfig.method}
                    onChange={(value) => handlePaginationConfigChange({ ...paginationConfig, method: value })}
                  >
                    <Option value="none">无分页</Option>
                    <Option value="url_pattern">URL 模板 (占位符展开)</Option>
                    <Option value="url_param">URL 参数 (?page=N)</Option>
                    <Option value="click_element">HTTP 点击 (抓 next href)</Option>
                    <Option value="js_click">JS 点击 (Playwright)</Option>
                    <Option value="infinite_scroll">无限滚动 (Playwright)</Option>
                  </Select>
                </Form.Item>
              </Col>
              <Col span={8}>
                <Form.Item label="起始页码" style={{ marginBottom: 12 }} tooltip="URL 里含 {N} 时以 N 为准，本字段忽略">
                  <InputNumber
                    min={0}
                    max={100}
                    style={{ width: '100%' }}
                    value={paginationConfig.start_page}
                    onChange={(value) => handlePaginationConfigChange({ ...paginationConfig, start_page: value || 1 })}
                  />
                </Form.Item>
              </Col>
              <Col span={8}>
                <Form.Item label="最大页数" style={{ marginBottom: 12 }}>
                  <InputNumber
                    min={1}
                    max={1000}
                    style={{ width: '100%' }}
                    value={paginationConfig.max_pages}
                    onChange={(value) => handlePaginationConfigChange({ ...paginationConfig, max_pages: value || 10 })}
                  />
                </Form.Item>
              </Col>
            </Row>

            {/* url_pattern: URL 模板 */}
            {paginationConfig.method === 'url_pattern' && (
              <Form.Item
                label="URL 模板 (可选)"
                tooltip='留空则用"目标URL"字段。示例：/list/page-{1}.html 或 /page/{page}/'
                style={{ marginBottom: 0 }}
              >
                <Input
                  placeholder="留空则用目标URL，或另填如 /list/page-{1}.html"
                  value={paginationConfig.url_template}
                  onChange={(e) => handlePaginationConfigChange({ ...paginationConfig, url_template: e.target.value })}
                />
              </Form.Item>
            )}

            {/* url_param: query 参数名 */}
            {paginationConfig.method === 'url_param' && (
              <Form.Item label="页码参数名" tooltip="拼到 URL query 上，例如 page → ?page=1&page=2" style={{ marginBottom: 0 }}>
                <Input
                  placeholder="page"
                  value={paginationConfig.page_param || 'page'}
                  onChange={(e) => handlePaginationConfigChange({ ...paginationConfig, page_param: e.target.value })}
                />
              </Form.Item>
            )}

            {/* click_element / js_click: next 选择器 + 类型 */}
            {(paginationConfig.method === 'click_element' || paginationConfig.method === 'js_click') && (
              <>
                <Row gutter={16}>
                  <Col span={6}>
                    <Form.Item label="选择器类型" style={{ marginBottom: 12 }}>
                      <Select
                        value={normalizeNextRule(paginationConfig.next_page_rule).type}
                        onChange={(t) => {
                          const cur = normalizeNextRule(paginationConfig.next_page_rule)
                          handlePaginationConfigChange({
                            ...paginationConfig,
                            next_page_rule: { type: t, expr: cur.expr },
                          })
                        }}
                      >
                        <Option value="css">CSS</Option>
                        <Option value="xpath">XPath</Option>
                        {paginationConfig.method === 'js_click' && <Option value="text">Text (Playwright)</Option>}
                      </Select>
                    </Form.Item>
                  </Col>
                  <Col span={18}>
                    <Form.Item
                      label="下一页表达式"
                      style={{ marginBottom: 12 }}
                      tooltip={
                        paginationConfig.method === 'js_click'
                          ? '例：li.next a | //li[@class=\'next\']/a | text=下一页'
                          : '例：li.next a | //li[@class=\'next\']/a'
                      }
                    >
                      <Input
                        placeholder={
                          normalizeNextRule(paginationConfig.next_page_rule).type === 'css'
                            ? '例: li.next a'
                            : normalizeNextRule(paginationConfig.next_page_rule).type === 'xpath'
                            ? '例: //li[@class=\'next\']/a'
                            : '例: 下一页'
                        }
                        value={normalizeNextRule(paginationConfig.next_page_rule).expr}
                        onChange={(e) => {
                          const cur = normalizeNextRule(paginationConfig.next_page_rule)
                          handlePaginationConfigChange({
                            ...paginationConfig,
                            next_page_rule: { type: cur.type, expr: e.target.value },
                          })
                        }}
                      />
                    </Form.Item>
                  </Col>
                </Row>
                {paginationConfig.method === 'js_click' && (
                  <Form.Item label="点击后等待(ms)" style={{ marginBottom: 0 }} tooltip="点击 next 后等 networkidle + 该毫秒数">
                    <InputNumber
                      min={0}
                      max={30000}
                      step={500}
                      style={{ width: 200 }}
                      value={paginationConfig.wait_after_click_ms || 1000}
                      onChange={(value) => handlePaginationConfigChange({ ...paginationConfig, wait_after_click_ms: value || 1000 })}
                    />
                  </Form.Item>
                )}
              </>
            )}

            {/* infinite_scroll: 滚动次数 + 每次等待 */}
            {paginationConfig.method === 'infinite_scroll' && (
              <Row gutter={16}>
                <Col span={12}>
                  <Form.Item label="滚动次数" style={{ marginBottom: 0 }}>
                    <InputNumber
                      min={1}
                      max={100}
                      style={{ width: '100%' }}
                      value={paginationConfig.scroll_count || 5}
                      onChange={(value) => handlePaginationConfigChange({ ...paginationConfig, scroll_count: value || 5 })}
                    />
                  </Form.Item>
                </Col>
                <Col span={12}>
                  <Form.Item label="每次滚动等待(ms)" style={{ marginBottom: 0 }}>
                    <InputNumber
                      min={0}
                      max={10000}
                      step={100}
                      style={{ width: '100%' }}
                      value={paginationConfig.scroll_wait_ms || 800}
                      onChange={(value) => handlePaginationConfigChange({ ...paginationConfig, scroll_wait_ms: value || 800 })}
                    />
                  </Form.Item>
                </Col>
              </Row>
            )}

            {/* 老 method 兼容提示 */}
            {(paginationConfig.method === 'javascript' || paginationConfig.method === 'ajax') && (
              <div style={{ padding: 8, background: 'rgba(0,0,0,0.02)', borderRadius: 4 }}>
                <Text type="secondary" style={{ fontSize: 12 }}>
                  旧配置：<code>{paginationConfig.method}</code> 已迁移为 <code>infinite_scroll</code>（Playwright 引擎）。
                  建议切换到 <code>js_click</code> 或 <code>infinite_scroll</code> 更精确。
                </Text>
              </div>
            )}
          </Card>
        </Space>
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
                <Tag color="default" style={{ marginLeft: 8 }}>
                  暂未生效
                </Tag>
              </Space>
            }
            size="small"
            /* T7-死列 (P2 附): anti_spider 目前不在 to_dispatch_dict 下发的
               字段里（core/domain/models/project.py:209-211 已声明），此处
               标注避免用户配置后期待生效。等 dispatcher 补齐后移除该标注。 */
          >
            <Form.Item
              label="启用反爬虫"
              style={{ marginBottom: 16 }}
              extra="当前版本未参与派发下发；配置会保存但不影响爬虫行为"
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
            
            <Form.Item
              name="dont_filter"
              label="URL去重"
              tooltip="是否启用URL去重功能，避免重复采集"
              initialValue={false}
            >
              <Select style={{ width: 200 }}>
                <Option value={false}>启用去重</Option>
                <Option value={true}>禁用去重</Option>
              </Select>
            </Form.Item>
          </Card>

          {/* 代理配置 */}
          <Card 
            title={
              <Space>
                <SafetyOutlined />
                代理配置
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
              <Row gutter={16}>
                <Col span={6}>
                  <Form.Item label="代理类型" style={{ marginBottom: 12 }}>
                    <Select
                      value={proxyConfig.proxy_type}
                      onChange={(value) => setProxyConfig({ ...proxyConfig, proxy_type: value })}
                      style={{ width: '100%' }}
                      options={[
                        { value: 'http', label: 'HTTP' },
                        { value: 'https', label: 'HTTPS' },
                        { value: 'socks4', label: 'SOCKS4' },
                        { value: 'socks5', label: 'SOCKS5' }
                      ]}
                    />
                  </Form.Item>
                </Col>
                <Col span={10}>
                  <Form.Item label="代理地址" style={{ marginBottom: 12 }}>
                    <Input
                      value={proxyConfig.proxy_url}
                      onChange={(e) => setProxyConfig({ ...proxyConfig, proxy_url: e.target.value })}
                      placeholder="http://proxy.example.com:8080"
                    />
                  </Form.Item>
                </Col>
                <Col span={4}>
                  <Form.Item label="用户名" style={{ marginBottom: 12 }}>
                    <Input
                      value={proxyConfig.username}
                      onChange={(e) => setProxyConfig({ ...proxyConfig, username: e.target.value })}
                      placeholder="可选"
                    />
                  </Form.Item>
                </Col>
                <Col span={4}>
                  <Form.Item label="密码" style={{ marginBottom: 12 }}>
                    <Input.Password
                      value={proxyConfig.password}
                      onChange={(e) => setProxyConfig({ ...proxyConfig, password: e.target.value })}
                      placeholder="可选"
                    />
                  </Form.Item>
                </Col>
              </Row>
            )}
          </Card>

          {/* S5: 内容去重 */}
          <Card
            title="内容去重"
            size="small"
            extra={
              <Text type="secondary" style={{ fontSize: 12 }}>
                按 item 字段哈希跨 run 持久化去重（存 Redis SET）
              </Text>
            }
          >
            <Form.Item label="启用去重" style={{ marginBottom: 16 }}>
              <Radio.Group
                value={dedupConfig.enabled ?? false}
                onChange={(e) => setDedupConfig({ ...dedupConfig, enabled: e.target.value })}
              >
                <Radio value={false}>关闭</Radio>
                <Radio value={true}>开启</Radio>
              </Radio.Group>
            </Form.Item>
            {dedupConfig.enabled && (
              <>
                <Form.Item
                  label="去重字段"
                  tooltip="按顺序参与 SHA256 哈希；命中即 DropItem"
                  style={{ marginBottom: 12 }}
                >
                  <Select
                    mode="tags"
                    style={{ width: '100%' }}
                    placeholder="回车添加字段名，如 title / detail_url"
                    value={dedupConfig.fields || []}
                    onChange={(v: string[]) => setDedupConfig({ ...dedupConfig, fields: v })}
                    tokenSeparators={[',', ' ']}
                  />
                </Form.Item>
                <Row gutter={16}>
                  <Col span={8}>
                    <Form.Item label="作用域" style={{ marginBottom: 0 }}>
                      <Select
                        value={dedupConfig.scope || 'project'}
                        onChange={(v: 'project' | 'run') => setDedupConfig({ ...dedupConfig, scope: v })}
                      >
                        <Option value="project">项目 (跨 run)</Option>
                        <Option value="run">本次 run</Option>
                      </Select>
                    </Form.Item>
                  </Col>
                  <Col span={8}>
                    <Form.Item label="TTL (天，0=永久)" style={{ marginBottom: 0 }}>
                      <InputNumber
                        min={0}
                        max={365}
                        style={{ width: '100%' }}
                        value={dedupConfig.ttl_days ?? 30}
                        onChange={(v) => setDedupConfig({ ...dedupConfig, ttl_days: v || 0 })}
                      />
                    </Form.Item>
                  </Col>
                  <Col span={8}>
                    <Form.Item label="命中动作" style={{ marginBottom: 0 }}>
                      <Select
                        value={dedupConfig.on_hit || 'drop'}
                        onChange={(v: 'drop' | 'log') => setDedupConfig({ ...dedupConfig, on_hit: v })}
                      >
                        <Option value="drop">丢弃 (drop)</Option>
                        <Option value="log">仅记录 (log)</Option>
                      </Select>
                    </Form.Item>
                  </Col>
                </Row>
              </>
            )}
          </Card>

          {/* S3b: scrapy-redis 断点续爬 */}
          <Card
            title="断点续爬 / 分布式"
            size="small"
            extra={
              <Text type="secondary" style={{ fontSize: 12 }}>
                启用后请求队列/去重集持久化到 Redis（scrapy-redis）
              </Text>
            }
          >
            <Form.Item
              label="启用断点续爬"
              style={{ marginBottom: 0 }}
              tooltip="取消/崩溃后队列保留；多 Worker 消费同一队列自然分片"
            >
              <Radio.Group
                value={resumeEnabled}
                onChange={(e) => setResumeEnabled(e.target.value)}
              >
                <Radio value={false}>关闭</Radio>
                <Radio value={true}>开启</Radio>
              </Radio.Group>
            </Form.Item>
          </Card>
        </Space>
      )
    }
  ]

  return (
    <div>
      <div style={{ textAlign: 'center', marginBottom: 24 }}>
        <Title level={4}>
          <ThunderboltOutlined style={{ marginRight: 8, color: '#52c41a' }} />
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
          engine: initialEngine,
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
