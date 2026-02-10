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
  Collapse,
  Tag,
  Switch,
  theme
} from 'antd'
import {
  PlusOutlined,
  DeleteOutlined,
  CodeOutlined,
  QuestionCircleOutlined
} from '@ant-design/icons'
import { useThemeContext } from '@/contexts/ThemeContext'
import type { ExtractionRule } from '@/types'

const { Text } = Typography

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
  const { token } = theme.useToken()
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

  const getHelpTextStyle = () => ({
    fontSize: 12,
    color: isDark ? 'rgba(255, 255, 255, 0.65)' : '#666',
    marginTop: 4
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
  type RuleField = 'desc' | 'type' | 'expr' | 'page_type' | 'attribute' | 'transform'

  const updateFormData = (field: RuleField, value: string) => {
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

  // 选择器类型选项
  const selectorTypes = [
    { value: 'css', label: 'CSS选择器', example: '.title, #content' },
    { value: 'xpath', label: 'XPath', example: '//h1[@class="title"]' },
    { value: 'regex', label: '正则表达式', example: '<title>(.*?)</title>' },
    { value: 'jsonpath', label: 'JSONPath', example: '$.data.title' }
  ]

  // 属性类型选项
  const attributeTypes = [
    { value: 'text', label: '文本内容' },
    { value: 'html', label: 'HTML内容' },
    { value: 'href', label: '链接地址' },
    { value: 'src', label: '图片地址' },
    { value: 'title', label: '标题属性' },
    { value: 'alt', label: 'Alt属性' },
    { value: 'data-*', label: '自定义属性' }
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
          <Collapse 
            size="small" 
            ghost
            items={rules.map((rule, index) => ({
              key: String(index),
              label: (
                <Space size="small">
                  <Text strong>{rule.desc}</Text>
                  <Tag color={getRuleTypeColor(rule.type)} style={{ margin: 0 }}>
                    {rule.type.toUpperCase()}
                  </Tag>
                  {rule.page_type && (
                    <Tag color={rule.page_type === 'list' ? 'blue' : 'orange'} style={{ margin: 0 }}>
                      {rule.page_type === 'list' ? '列表' : '详情'}
                    </Tag>
                  )}
                </Space>
              ),
              extra: (
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
              ),
              children: (
                <Space direction="vertical" size={4} style={{ width: '100%' }}>
                  <div>
                    <Text type="secondary" style={{ fontSize: 12 }}>表达式: </Text>
                    <Text code copyable={{ text: rule.expr }} style={{ fontSize: 12 }}>
                      {rule.expr}
                    </Text>
                  </div>
                  {rule.attribute && (
                    <div>
                      <Text type="secondary" style={{ fontSize: 12 }}>属性: </Text>
                      <Text style={{ fontSize: 12 }}>{rule.attribute}</Text>
                    </div>
                  )}
                  {rule.transform && (
                    <div>
                      <Text type="secondary" style={{ fontSize: 12 }}>转换: </Text>
                      <Text code style={{ fontSize: 12 }}>{rule.transform}</Text>
                    </div>
                  )}
                </Space>
              )
            }))}
          />
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
              <div style={{ marginBottom: 12 }}>
                <label style={{ display: 'block', marginBottom: 4, fontWeight: 500 }}>
                  字段描述
                </label>
                <Input 
                  placeholder="如: 标题, 时间, 作者"
                  value={formData.desc}
                  onChange={(e) => updateFormData('desc', e.target.value)}
                  status={errors.desc ? 'error' : ''}
                />
                {errors.desc && (
                  <div style={{ color: token.colorError, fontSize: 12, marginTop: 2 }}>
                    {errors.desc}
                  </div>
                )}
              </div>
            </Col>
            <Col span={showPageType ? 8 : 12}>
              <div style={{ marginBottom: 12 }}>
                <label style={{ display: 'block', marginBottom: 4, fontWeight: 500 }}>
                  选择器类型
                </label>
                <Select 
                  value={formData.type} 
                  onChange={(value) => updateFormData('type', value)}
                  style={{ width: '100%' }}
                  options={selectorTypes.map(t => ({ value: t.value, label: t.label }))}
                />
              </div>
            </Col>
            {showPageType && (
              <Col span={8}>
                <div style={{ marginBottom: 12 }}>
                  <label style={{ display: 'block', marginBottom: 4, fontWeight: 500 }}>
                    页面类型
                  </label>
                  <Select 
                    value={formData.page_type} 
                    onChange={(value) => updateFormData('page_type', value)}
                    style={{ width: '100%' }}
                    options={[
                      { value: 'list', label: '列表页' },
                      { value: 'detail', label: '详情页' }
                    ]}
                  />
                </div>
              </Col>
            )}
          </Row>

          <div style={{ marginBottom: 12 }}>
            <label style={{ display: 'block', marginBottom: 4, fontWeight: 500 }}>
              选择器表达式
              <Text type="secondary" style={{ fontWeight: 400, marginLeft: 8, fontSize: 12 }}>
                {selectorTypes.find(t => t.value === formData.type)?.example}
              </Text>
            </label>
            <Input 
              placeholder={selectorTypes.find(t => t.value === formData.type)?.example || '输入表达式'}
              prefix={<CodeOutlined style={{ color: token.colorTextTertiary }} />}
              value={formData.expr}
              onChange={(e) => updateFormData('expr', e.target.value)}
              status={errors.expr ? 'error' : ''}
            />
            {errors.expr && (
              <div style={{ color: token.colorError, fontSize: 12, marginTop: 2 }}>
                {errors.expr}
              </div>
            )}
          </div>

          {/* 高级选项 */}
          <div style={{ marginBottom: 12 }}>
            <Space size="small">
              <Text type="secondary" style={{ fontSize: 13 }}>高级选项</Text>
              <Switch size="small" checked={showAdvanced} onChange={setShowAdvanced} />
            </Space>
          </div>

          {showAdvanced && (
            <Row gutter={16}>
              <Col span={12}>
                <div style={{ marginBottom: 12 }}>
                  <label style={{ display: 'block', marginBottom: 4, fontWeight: 500 }}>
                    提取属性
                  </label>
                  <Select 
                    placeholder="默认文本内容" 
                    allowClear
                    value={formData.attribute || undefined}
                    onChange={(value) => updateFormData('attribute', value)}
                    style={{ width: '100%' }}
                    options={attributeTypes.map(a => ({ value: a.value, label: a.label }))}
                  />
                </div>
              </Col>
              <Col span={12}>
                <div style={{ marginBottom: 12 }}>
                  <label style={{ display: 'block', marginBottom: 4, fontWeight: 500 }}>
                    转换规则
                  </label>
                  <Input 
                    placeholder="strip(), upper()" 
                    value={formData.transform}
                    onChange={(e) => updateFormData('transform', e.target.value)}
                  />
                </div>
              </Col>
            </Row>
          )}

          <Button
            type="primary"
            icon={<PlusOutlined />}
            onClick={handleAddRule}
            block
            style={{ marginTop: 4 }}
          >
            添加规则
          </Button>
        </div>
      </Card>

      {/* 提示信息 */}
      {required && rules.length === 0 && (
        <Alert
          message="至少需要添加一个提取规则"
          type="warning"
          showIcon
          style={{ marginTop: 12 }}
        />
      )}

      {/* 选择器示例 - 可折叠卡片 */}
      <Collapse 
        size="small" 
        style={{ marginTop: 12 }}
        items={[{
          key: 'examples',
          label: (
            <Space size="small">
              <QuestionCircleOutlined />
              <span>选择器示例</span>
            </Space>
          ),
          children: (
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
                </div>
              </Col>
              <Col span={6}>
                <Text strong>正则表达式:</Text>
                <div style={getHelpTextStyle()}>
                  <div>• &lt;title&gt;(.*?)&lt;/title&gt;</div>
                  <div>• href="([^"]*)"</div>
                  <div>• (\d{'{'}4{'}'}-\d{'{'}2{'}'}-\d{'{'}2{'}'})</div>
                  <div>• price:\s*(\d+\.?\d*)</div>
                </div>
              </Col>
              <Col span={6}>
                <Text strong>JSONPath:</Text>
                <div style={getHelpTextStyle()}>
                  <div>• $.data.title</div>
                  <div>• $..items[*].name</div>
                  <div>• $.result[0]</div>
                  <div>• $..author</div>
                </div>
              </Col>
            </Row>
          )
        }]}
      />
    </div>
  )
}

export default RuleSelector
