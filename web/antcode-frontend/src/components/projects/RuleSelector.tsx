import React, { useState } from 'react'
import {
  Card,
  Input,
  Select,
  Button,
  Space,
  Typography,
  Row,
  Col,
  Alert,
  Tooltip,
  Collapse,
  Tag,
  Switch
} from 'antd'
import {
  PlusOutlined,
  DeleteOutlined,
  InfoCircleOutlined,
  CodeOutlined,
  QuestionCircleOutlined
} from '@ant-design/icons'
import { useThemeContext } from '@/contexts/ThemeContext'
import type { ExtractionRule } from '@/types'

const { Text } = Typography
const { Option } = Select
const { TextArea } = Input
const { Panel } = Collapse

interface RuleSelectorProps {
  rules: ExtractionRule[]
  onChange: (rules: ExtractionRule[]) => void
  placeholder?: string
  required?: boolean
  showPageType?: boolean  // 是否显示页面类型选择（混合模式下使用）
  defaultPageType?: 'list' | 'detail'  // 默认页面类型
}

const RuleSelector: React.FC<RuleSelectorProps> = ({
  rules,
  onChange,
  placeholder = "添加提取规则",
  required = false,
  showPageType = false,
  defaultPageType
}) => {
  const { isDark } = useThemeContext()
  const [showAdvanced, setShowAdvanced] = useState(false)
  
  // 表单数据状态
  const [formData, setFormData] = useState({
    desc: '',
    type: 'css',
    expr: '',
    page_type: defaultPageType || 'list',
    attribute: '',
    transform: ''
  })
  
  // 错误状态
  const [errors, setErrors] = useState<Record<string, string>>({})

  // 动态样式函数
  const getCardStyle = () => ({
    backgroundColor: isDark ? '#1f1f1f' : '#fafafa',
    borderColor: isDark ? '#434343' : '#d9d9d9'
  })

  const getHelpCardStyle = () => ({
    marginTop: 16,
    backgroundColor: isDark ? '#141414' : '#f9f9f9',
    borderColor: isDark ? '#434343' : '#d9d9d9'
  })

  const getHelpTextStyle = () => ({
    fontSize: 12,
    color: isDark ? 'rgba(255, 255, 255, 0.65)' : '#666',
    marginTop: 4
  })

  const getIconStyle = () => ({
    color: isDark ? 'rgba(255, 255, 255, 0.45)' : '#999'
  })

  // 验证表单数据
  const validateForm = () => {
    const newErrors: Record<string, string> = {}
    
    if (!formData.desc.trim()) {
      newErrors.desc = '请输入字段描述'
    }
    
    if (!formData.expr.trim()) {
      newErrors.expr = '请输入选择器表达式'
    }
    
    setErrors(newErrors)
    return Object.keys(newErrors).length === 0
  }

  // 更新表单数据
  const updateFormData = (field: string, value: any) => {
    setFormData(prev => ({ ...prev, [field]: value }))
    // 清除该字段的错误
    if (errors[field]) {
      setErrors(prev => ({ ...prev, [field]: '' }))
    }
  }

  // 重置表单
  const resetForm = () => {
    setFormData({
      desc: '',
      type: 'css',
      expr: '',
      page_type: defaultPageType || 'list',
      attribute: '',
      transform: ''
    })
    setErrors({})
    setShowAdvanced(false)
  }

  // 添加新规则
  const handleAddRule = () => {
    if (!validateForm()) {
      return
    }
    
    const newRule: ExtractionRule = {
      desc: formData.desc,
      type: formData.type || 'css',
      expr: formData.expr,
      page_type: showPageType ? (formData.page_type || defaultPageType) : defaultPageType,
      attribute: formData.attribute || undefined,
      transform: formData.transform || undefined
    }
    
    onChange([...rules, newRule])
    resetForm()
  }
  const handleDeleteRule = (index: number) => {
    onChange(rules.filter((_, i) => i !== index))
  }

  // 更新规则
  const handleUpdateRule = (index: number, updatedRule: Partial<ExtractionRule>) => {
    onChange(rules.map((rule, i) => 
      i === index ? { ...rule, ...updatedRule } : rule
    ))
  }

  // 选择器类型选项
  const selectorTypes = [
    { value: 'css', label: 'CSS选择器', description: '使用CSS选择器语法，最常用' },
    { value: 'xpath', label: 'XPath', description: '使用XPath表达式，功能强大' },
    { value: 'regex', label: '正则表达式', description: '使用正则表达式匹配' },
    { value: 'jsonpath', label: 'JSONPath', description: '用于JSON数据提取' }
  ]

  // 属性类型选项
  const attributeTypes = [
    { value: 'text', label: '文本内容', description: '提取元素的文本内容' },
    { value: 'html', label: 'HTML内容', description: '提取元素的HTML内容' },
    { value: 'href', label: '链接地址', description: '提取链接的href属性' },
    { value: 'src', label: '图片地址', description: '提取图片的src属性' },
    { value: 'title', label: '标题属性', description: '提取元素的title属性' },
    { value: 'alt', label: 'Alt属性', description: '提取图片的alt属性' },
    { value: 'data-*', label: '自定义属性', description: '提取data-开头的自定义属性' }
  ]

  // 获取规则类型的颜色
  const getRuleTypeColor = (type: string) => {
    switch (type) {
      case 'css': return 'blue'
      case 'xpath': return 'green'
      case 'regex': return 'orange'
      case 'jsonpath': return 'purple'
      default: return 'default'
    }
  }

  return (
    <div>
      {/* 已添加的规则列表 */}
      {rules.length > 0 && (
        <div style={{ marginBottom: 16 }}>
          <Collapse size="small" ghost>
            {rules.map((rule, index) => (
              <Panel
                key={index}
                header={
                  <Space>
                    <Text strong>{rule.desc}</Text>
                    <Tag color={getRuleTypeColor(rule.type)}>
                      {rule.type.toUpperCase()}
                    </Tag>
                    {rule.page_type && (
                      <Tag color={rule.page_type === 'list' ? 'blue' : 'orange'}>
                        {rule.page_type === 'list' ? '列表页' : '详情页'}
                      </Tag>
                    )}
                    <Text code style={{ fontSize: 12 }}>
                      {rule.expr.length > 30 ? 
                        `${rule.expr.substring(0, 30)}...` : 
                        rule.expr
                      }
                    </Text>
                  </Space>
                }
                extra={
                  <Button
                    type="text"
                    danger
                    size="small"
                    icon={<DeleteOutlined />}
                    onClick={(e) => {
                      e.stopPropagation()
                      handleDeleteRule(index)
                    }}
                  />
                }
              >
                <Row gutter={16}>
                  <Col span={12}>
                    <Space direction="vertical" size="small" style={{ width: '100%' }}>
                      <div>
                        <Text type="secondary">选择器类型:</Text>
                        <br />
                        <Tag color={getRuleTypeColor(rule.type)}>
                          {selectorTypes.find(t => t.value === rule.type)?.label}
                        </Tag>
                      </div>
                      <div>
                        <Text type="secondary">表达式:</Text>
                        <br />
                        <Text code copyable>{rule.expr}</Text>
                      </div>
                      {rule.attribute && (
                        <div>
                          <Text type="secondary">提取属性:</Text>
                          <br />
                          <Text>{rule.attribute}</Text>
                        </div>
                      )}
                    </Space>
                  </Col>
                  <Col span={12}>
                    <Space direction="vertical" size="small" style={{ width: '100%' }}>
                      {rule.default && (
                        <div>
                          <Text type="secondary">默认值:</Text>
                          <br />
                          <Text>{rule.default}</Text>
                        </div>
                      )}
                      {rule.transform && (
                        <div>
                          <Text type="secondary">转换规则:</Text>
                          <br />
                          <Text code>{rule.transform}</Text>
                        </div>
                      )}
                      <div>
                        <Text type="secondary">是否必需:</Text>
                        <br />
                        <Text>{rule.required ? '是' : '否'}</Text>
                      </div>
                    </Space>
                  </Col>
                </Row>
              </Panel>
            ))}
          </Collapse>
        </div>
      )}

      {/* 添加新规则表单 */}
      <Card
        title={
          <Space>
            <PlusOutlined />
            {placeholder}
          </Space>
        }
        size="small"
        style={getCardStyle()}
      >
        <div>
          <Row gutter={16}>
            <Col span={showPageType ? 8 : 12}>
              <div style={{ marginBottom: 16 }}>
                <label style={{ 
                  display: 'block', 
                  marginBottom: 8, 
                  color: isDark ? 'rgba(255, 255, 255, 0.85)' : 'rgba(0, 0, 0, 0.85)',
                  fontWeight: 600
                }}>
                  字段描述 
                  <Tooltip title="描述要提取的数据字段，如：文章标题、发布时间等">
                    <InfoCircleOutlined style={{ marginLeft: 4, color: '#999' }} />
                  </Tooltip>
                </label>
                <Input 
                  placeholder="例如: 文章标题, 发布时间, 作者"
                  value={formData.desc}
                  onChange={(e) => updateFormData('desc', e.target.value)}
                  status={errors.desc ? 'error' : ''}
                />
                {errors.desc && (
                  <div style={{ color: '#ff4d4f', fontSize: '12px', marginTop: '4px' }}>
                    {errors.desc}
                  </div>
                )}
              </div>
            </Col>
            <Col span={showPageType ? 8 : 12}>
              <div style={{ marginBottom: 16 }}>
                <label style={{ 
                  display: 'block', 
                  marginBottom: 8, 
                  color: isDark ? 'rgba(255, 255, 255, 0.85)' : 'rgba(0, 0, 0, 0.85)',
                  fontWeight: 600
                }}>
                  选择器类型
                </label>
                <Select 
                  value={formData.type} 
                  onChange={(value) => updateFormData('type', value)}
                  style={{ width: '100%' }}
                >
                  {selectorTypes.map(type => (
                    <Option key={type.value} value={type.value}>
                      <Space>
                        <span>{type.label}</span>
                        <Tooltip title={type.description}>
                          <InfoCircleOutlined style={getIconStyle()} />
                        </Tooltip>
                      </Space>
                    </Option>
                  ))}
                </Select>
              </div>
            </Col>
            {showPageType && (
              <Col span={8}>
                <div style={{ marginBottom: 16 }}>
                  <label style={{ 
                    display: 'block', 
                    marginBottom: 8, 
                    color: isDark ? 'rgba(255, 255, 255, 0.85)' : 'rgba(0, 0, 0, 0.85)',
                    fontWeight: 600
                  }}>
                    页面类型 
                    <Tooltip title="规则适用的页面类型">
                      <InfoCircleOutlined style={{ marginLeft: 4, color: '#999' }} />
                    </Tooltip>
                  </label>
                  <Select 
                    value={formData.page_type} 
                    onChange={(value) => updateFormData('page_type', value)}
                    style={{ width: '100%' }}
                  >
                    <Option value="list">列表页</Option>
                    <Option value="detail">详情页</Option>
                  </Select>
                </div>
              </Col>
            )}
          </Row>

          <div style={{ marginBottom: 16 }}>
            <label style={{ 
              display: 'block', 
              marginBottom: 8, 
              color: isDark ? 'rgba(255, 255, 255, 0.85)' : 'rgba(0, 0, 0, 0.85)',
              fontWeight: 600
            }}>
              选择器表达式 
              <Tooltip title="根据选择的类型输入对应的表达式">
                <InfoCircleOutlined style={{ marginLeft: 4, color: '#999' }} />
              </Tooltip>
            </label>
            <Input 
              placeholder="例如: .title, //h1[@class='title'], <title>(.*?)</title>"
              prefix={<CodeOutlined />}
              value={formData.expr}
              onChange={(e) => updateFormData('expr', e.target.value)}
              status={errors.expr ? 'error' : ''}
            />
            {errors.expr && (
              <div style={{ color: '#ff4d4f', fontSize: '12px', marginTop: '4px' }}>
                {errors.expr}
              </div>
            )}
          </div>

          {/* 高级选项 */}
          <div style={{ marginBottom: 16 }}>
            <Space>
              <Text>高级选项</Text>
              <Switch
                size="small"
                checked={showAdvanced}
                onChange={setShowAdvanced}
              />
            </Space>
          </div>

          {showAdvanced && (
            <>
              <Row gutter={16}>
                <Col span={12}>
                  <div style={{ marginBottom: 16 }}>
                    <label style={{ 
                      display: 'block', 
                      marginBottom: 8, 
                      color: isDark ? 'rgba(255, 255, 255, 0.85)' : 'rgba(0, 0, 0, 0.85)',
                      fontWeight: 600
                    }}>
                      提取属性 
                      <Tooltip title="选择要提取的HTML属性，默认提取文本内容">
                        <InfoCircleOutlined style={{ marginLeft: 4, color: '#999' }} />
                      </Tooltip>
                    </label>
                    <Select 
                      placeholder="默认提取文本内容" 
                      allowClear
                      value={formData.attribute || undefined}
                      onChange={(value) => updateFormData('attribute', value)}
                      style={{ width: '100%' }}
                    >
                      {attributeTypes.map(attr => (
                        <Option key={attr.value} value={attr.value}>
                          <Space>
                            <span>{attr.label}</span>
                            <Tooltip title={attr.description}>
                              <InfoCircleOutlined style={getIconStyle()} />
                            </Tooltip>
                          </Space>
                        </Option>
                      ))}
                    </Select>
                  </div>
                </Col>
                <Col span={12}>
                  <div style={{ marginBottom: 16 }}>
                    <label style={{ 
                      display: 'block', 
                      marginBottom: 8, 
                      color: isDark ? 'rgba(255, 255, 255, 0.85)' : 'rgba(0, 0, 0, 0.85)',
                      fontWeight: 600
                    }}>
                      转换规则 
                      <Tooltip title="对提取的数据进行转换，如：strip()、replace('旧', '新')">
                        <InfoCircleOutlined style={{ marginLeft: 4, color: '#999' }} />
                      </Tooltip>
                    </label>
                    <Input 
                      placeholder="例如: strip(), upper()" 
                      value={formData.transform}
                      onChange={(e) => updateFormData('transform', e.target.value)}
                    />
                  </div>
                </Col>
              </Row>
            </>
          )}

          <div style={{ marginTop: 16 }}>
            <Button
              type="primary"
              icon={<PlusOutlined />}
              onClick={handleAddRule}
              block
            >
              添加规则
            </Button>
          </div>
        </div>
      </Card>

      {/* 提示信息 */}
      {required && rules.length === 0 && (
        <Alert
          message="至少需要添加一个提取规则"
          type="warning"
          showIcon
          style={{ marginTop: 16 }}
        />
      )}

      {rules.length > 0 && (
        <Alert
          message={`已添加 ${rules.length} 个提取规则`}
          type="info"
          showIcon
          style={{ marginTop: 16 }}
        />
      )}

      {/* 帮助信息 */}
      <Card
        title={
          <Space>
            <QuestionCircleOutlined />
            选择器示例
          </Space>
        }
        size="small"
        style={getHelpCardStyle()}
      >
        <Row gutter={16}>
          <Col span={6}>
            <Text strong>CSS选择器:</Text>
            <div style={getHelpTextStyle()}>
              <div>• .title (类选择器)</div>
              <div>• #content (ID选择器)</div>
              <div>• h1.title (标签+类)</div>
              <div>• a[href] (属性选择器)</div>
              <div>• div &gt; p (子元素)</div>
            </div>
          </Col>
          <Col span={6}>
            <Text strong>XPath:</Text>
            <div style={getHelpTextStyle()}>
              <div>• //h1[@class='title']</div>
              <div>• //div[@id='content']//text()</div>
              <div>• //a/@href</div>
              <div>• //p[contains(@class,'content')]</div>
              <div>• //div[position()=1]</div>
            </div>
          </Col>
          <Col span={6}>
            <Text strong>正则表达式:</Text>
            <div style={getHelpTextStyle()}>
              <div>• &lt;title&gt;(.*?)&lt;/title&gt;</div>
              <div>• href="([^"]*)"</div>
              <div>• (\d{4}-\d{2}-\d{2})</div>
              <div>• price:\s*(\d+\.?\d*)</div>
              <div>• &lt;p&gt;([\s\S]*?)&lt;/p&gt;</div>
            </div>
          </Col>
          <Col span={6}>
            <Text strong>JSONPath:</Text>
            <div style={getHelpTextStyle()}>
              <div>• $.data.title</div>
              <div>• $..items[*].name</div>
              <div>• $.result[?(@.price&gt;100)]</div>
              <div>• $..author</div>
              <div>• $.items[0:5]</div>
            </div>
          </Col>
        </Row>
      </Card>
    </div>
  )
}

export default RuleSelector