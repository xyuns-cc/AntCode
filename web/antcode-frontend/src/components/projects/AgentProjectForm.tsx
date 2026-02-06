import React, { useCallback, useState, useEffect } from 'react'
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
    Switch,
    Slider,
    Alert,
    Tag
} from 'antd'
import {
    RobotOutlined,
    GlobalOutlined,
    SettingOutlined,
    ThunderboltOutlined,
    DatabaseOutlined,
    ChromeOutlined,
    SafetyCertificateOutlined,
    ClockCircleOutlined
} from '@ant-design/icons'

const { Text } = Typography
const { TextArea } = Input

// AI 模型配置
const AI_MODELS = [
    {
        value: 'gpt-4o',
        label: 'GPT-4o',
        description: 'OpenAI 最强多模态模型，视觉理解能力出色',
        provider: 'OpenAI'
    },
    {
        value: 'gpt-4o-mini',
        label: 'GPT-4o Mini',
        description: '轻量快速，适合简单任务',
        provider: 'OpenAI'
    },
    {
        value: 'claude-3.5-sonnet',
        label: 'Claude 3.5 Sonnet',
        description: 'Anthropic 旗舰模型，代码能力强',
        provider: 'Anthropic'
    },
    {
        value: 'gemini-2.0-flash',
        label: 'Gemini 2.0 Flash',
        description: 'Google 最新模型，多模态理解',
        provider: 'Google'
    }
]

// 浏览器类型
const BROWSER_TYPES = [
    { value: 'chromium', label: 'Chromium', icon: <ChromeOutlined /> },
    { value: 'firefox', label: 'Firefox', icon: <GlobalOutlined /> }
]

// 输出格式
const OUTPUT_FORMATS = [
    { value: 'json', label: 'JSON' },
    { value: 'csv', label: 'CSV' },
    { value: 'markdown', label: 'Markdown' }
]

interface AgentProjectFormProps {
    initialData?: Record<string, unknown>
    onDataChange?: (data: Record<string, unknown>) => void
    onSubmit: (data: Record<string, unknown>) => void
    loading?: boolean
    isEdit?: boolean
    onRef?: (ref: { submit: () => void }) => void
    onValidationChange?: (isValid: boolean, tooltip: string) => void
}

const AgentProjectForm: React.FC<AgentProjectFormProps> = ({
    initialData = {},
    onDataChange,
    onSubmit,
    loading: _loading = false,
    isEdit: _isEdit = false,
    onRef,
    onValidationChange
}) => {
    const [form] = Form.useForm()
    const [activeTab, setActiveTab] = useState('basic')

    // 暴露 submit 方法给父组件
    useEffect(() => {
        if (onRef) {
            onRef({
                submit: () => form.submit()
            })
        }
    }, [form, onRef])

    // 验证表单并通知父组件
    const validateForm = useCallback(async () => {
        if (!onValidationChange) return

        try {
            await form.validateFields(['target_url', 'task_description', 'ai_model'])
            onValidationChange(true, '')
        } catch {
            onValidationChange(false, '请填写必填字段')
        }
    }, [form, onValidationChange])

    // 初始验证
    useEffect(() => {
        validateForm()
    }, [validateForm])

    const handleFinish = useCallback((values: Record<string, unknown>) => {
        const submitData = {
            ...values,
            type: 'agent'
        }
        onSubmit(submitData)
    }, [onSubmit])

    const handleValuesChange = useCallback(
        (_changedValues: Record<string, unknown>, allValues: Record<string, unknown>) => {
            if (onDataChange) {
                onDataChange(allValues)
            }
            validateForm()
        },
        [onDataChange, validateForm]
    )

    const tabItems = [
        {
            key: 'basic',
            label: (
                <Space>
                    <RobotOutlined />
                    <span>AI 任务配置</span>
                </Space>
            ),
            children: (
                <Space direction="vertical" size="large" style={{ width: '100%' }}>
                    <Alert
                        message="智能代理模式"
                        description="使用 AI 模型理解网页内容并自动执行数据采集任务。只需描述您想要获取的数据，AI 将自动完成浏览、点击、提取等操作。"
                        type="info"
                        showIcon
                        icon={<RobotOutlined />}
                    />

                    <Card size="small" title={<Space><GlobalOutlined /> 目标配置</Space>}>
                        <Form.Item
                            name="target_url"
                            label="起始 URL"
                            rules={[
                                { required: true, message: '请输入起始 URL' },
                                { type: 'url', message: '请输入有效的 URL' }
                            ]}
                            tooltip="AI 代理将从此页面开始执行任务"
                        >
                            <Input placeholder="https://example.com" prefix={<GlobalOutlined />} />
                        </Form.Item>

                        <Form.Item
                            name="task_description"
                            label="任务描述"
                            rules={[{ required: true, message: '请描述您的采集任务' }]}
                            tooltip="用自然语言描述您想要采集的数据"
                        >
                            <TextArea
                                rows={4}
                                placeholder="例如：从这个电商网站采集所有商品的名称、价格、图片和评分。需要翻页获取所有数据。"
                            />
                        </Form.Item>

                        <Form.Item
                            name="data_schema"
                            label="数据结构（可选）"
                            tooltip="定义期望的输出数据结构，JSON 格式"
                        >
                            <TextArea
                                rows={4}
                                placeholder='{"name": "商品名称", "price": "价格", "image": "图片URL", "rating": "评分"}'
                                style={{ fontFamily: 'monospace' }}
                            />
                        </Form.Item>
                    </Card>

                    <Card size="small" title={<Space><ThunderboltOutlined /> AI 模型配置</Space>}>
                        <Form.Item
                            name="ai_model"
                            label="AI 模型"
                            rules={[{ required: true, message: '请选择 AI 模型' }]}
                            initialValue="gpt-4o"
                        >
                            <Select placeholder="选择 AI 模型">
                                {AI_MODELS.map(model => (
                                    <Select.Option key={model.value} value={model.value}>
                                        <Space>
                                            <Tag color="blue">{model.provider}</Tag>
                                            <span>{model.label}</span>
                                            <Text type="secondary" style={{ fontSize: 12 }}>{model.description}</Text>
                                        </Space>
                                    </Select.Option>
                                ))}
                            </Select>
                        </Form.Item>

                        <Row gutter={16}>
                            <Col span={12}>
                                <Form.Item
                                    name="max_tokens"
                                    label="最大生成长度"
                                    initialValue={4096}
                                    tooltip="控制 AI 单次响应的最大 token 数"
                                >
                                    <InputNumber min={256} max={16384} style={{ width: '100%' }} />
                                </Form.Item>
                            </Col>
                            <Col span={12}>
                                <Form.Item
                                    name="temperature"
                                    label="创造性"
                                    initialValue={0.7}
                                    tooltip="较高值使输出更随机，较低值使输出更确定"
                                >
                                    <Slider min={0} max={1} step={0.1} marks={{ 0: '精确', 0.5: '平衡', 1: '创意' }} />
                                </Form.Item>
                            </Col>
                        </Row>

                        <Form.Item
                            name="system_prompt"
                            label="系统提示词（可选）"
                            tooltip="自定义 AI 的行为指令"
                        >
                            <TextArea
                                rows={3}
                                placeholder="可选：添加额外的指令来指导 AI 的行为..."
                            />
                        </Form.Item>
                    </Card>
                </Space>
            )
        },
        {
            key: 'browser',
            label: (
                <Space>
                    <ChromeOutlined />
                    <span>浏览器配置</span>
                </Space>
            ),
            children: (
                <Space direction="vertical" size="large" style={{ width: '100%' }}>
                    <Card size="small" title={<Space><ChromeOutlined /> 浏览器设置</Space>}>
                        <Row gutter={16}>
                            <Col span={12}>
                                <Form.Item
                                    name="browser_type"
                                    label="浏览器类型"
                                    initialValue="chromium"
                                >
                                    <Select>
                                        {BROWSER_TYPES.map(type => (
                                            <Select.Option key={type.value} value={type.value}>
                                                <Space>{type.icon} {type.label}</Space>
                                            </Select.Option>
                                        ))}
                                    </Select>
                                </Form.Item>
                            </Col>
                            <Col span={12}>
                                <Form.Item
                                    name="headless"
                                    label="无头模式"
                                    valuePropName="checked"
                                    initialValue={true}
                                    tooltip="无头模式下浏览器在后台运行，不显示窗口"
                                >
                                    <Switch checkedChildren="开启" unCheckedChildren="关闭" />
                                </Form.Item>
                            </Col>
                        </Row>

                        <Divider orientation="left" plain>视口设置</Divider>

                        <Row gutter={16}>
                            <Col span={12}>
                                <Form.Item
                                    name="viewport_width"
                                    label="宽度"
                                    initialValue={1920}
                                >
                                    <InputNumber min={320} max={3840} style={{ width: '100%' }} addonAfter="px" />
                                </Form.Item>
                            </Col>
                            <Col span={12}>
                                <Form.Item
                                    name="viewport_height"
                                    label="高度"
                                    initialValue={1080}
                                >
                                    <InputNumber min={480} max={2160} style={{ width: '100%' }} addonAfter="px" />
                                </Form.Item>
                            </Col>
                        </Row>

                        <Form.Item
                            name="user_agent"
                            label="User-Agent（可选）"
                            tooltip="自定义浏览器标识"
                        >
                            <Input placeholder="留空使用默认值" />
                        </Form.Item>
                    </Card>

                    <Card size="small" title={<Space><SafetyCertificateOutlined /> 代理配置</Space>}>
                        <Form.Item
                            name="proxy_enabled"
                            label="启用代理"
                            valuePropName="checked"
                            initialValue={false}
                        >
                            <Switch checkedChildren="开启" unCheckedChildren="关闭" />
                        </Form.Item>

                        <Form.Item
                            noStyle
                            shouldUpdate={(prevValues, currentValues) =>
                                prevValues.proxy_enabled !== currentValues.proxy_enabled
                            }
                        >
                            {({ getFieldValue }) =>
                                getFieldValue('proxy_enabled') && (
                                    <Form.Item
                                        name="proxy_url"
                                        label="代理地址"
                                        rules={[{ required: true, message: '请输入代理地址' }]}
                                    >
                                        <Input placeholder="http://proxy.example.com:8080" />
                                    </Form.Item>
                                )
                            }
                        </Form.Item>
                    </Card>
                </Space>
            )
        },
        {
            key: 'strategy',
            label: (
                <Space>
                    <SettingOutlined />
                    <span>采集策略</span>
                </Space>
            ),
            children: (
                <Space direction="vertical" size="large" style={{ width: '100%' }}>
                    <Card size="small" title={<Space><ClockCircleOutlined /> 执行策略</Space>}>
                        <Row gutter={16}>
                            <Col span={8}>
                                <Form.Item
                                    name="max_pages"
                                    label="最大页面数"
                                    initialValue={10}
                                    tooltip="AI 最多访问的页面数量"
                                >
                                    <InputNumber min={1} max={1000} style={{ width: '100%' }} />
                                </Form.Item>
                            </Col>
                            <Col span={8}>
                                <Form.Item
                                    name="request_delay"
                                    label="请求间隔"
                                    initialValue={1000}
                                    tooltip="每次操作之间的等待时间"
                                >
                                    <InputNumber min={0} max={60000} style={{ width: '100%' }} addonAfter="ms" />
                                </Form.Item>
                            </Col>
                            <Col span={8}>
                                <Form.Item
                                    name="timeout"
                                    label="超时时间"
                                    initialValue={30000}
                                    tooltip="单个页面的最大等待时间"
                                >
                                    <InputNumber min={1000} max={300000} style={{ width: '100%' }} addonAfter="ms" />
                                </Form.Item>
                            </Col>
                        </Row>

                        <Row gutter={16}>
                            <Col span={12}>
                                <Form.Item
                                    name="retry_count"
                                    label="重试次数"
                                    initialValue={3}
                                    tooltip="操作失败时的重试次数"
                                >
                                    <InputNumber min={0} max={10} style={{ width: '100%' }} />
                                </Form.Item>
                            </Col>
                            <Col span={12}>
                                <Form.Item
                                    name="max_steps"
                                    label="最大操作步数"
                                    initialValue={100}
                                    tooltip="AI 最多执行的操作数量"
                                >
                                    <InputNumber min={1} max={1000} style={{ width: '100%' }} />
                                </Form.Item>
                            </Col>
                        </Row>

                        <Form.Item
                            name="stop_conditions"
                            label="停止条件（可选）"
                            tooltip="满足条件时停止采集"
                        >
                            <TextArea
                                rows={2}
                                placeholder="例如：采集到 1000 条数据时停止；遇到登录页面时停止"
                            />
                        </Form.Item>
                    </Card>

                    <Card size="small" title={<Space><DatabaseOutlined /> 输出配置</Space>}>
                        <Row gutter={16}>
                            <Col span={12}>
                                <Form.Item
                                    name="output_format"
                                    label="输出格式"
                                    initialValue="json"
                                >
                                    <Select>
                                        {OUTPUT_FORMATS.map(format => (
                                            <Select.Option key={format.value} value={format.value}>
                                                {format.label}
                                            </Select.Option>
                                        ))}
                                    </Select>
                                </Form.Item>
                            </Col>
                            <Col span={12}>
                                <Form.Item
                                    name="save_screenshots"
                                    label="保存截图"
                                    valuePropName="checked"
                                    initialValue={false}
                                    tooltip="保存每个页面的截图用于调试"
                                >
                                    <Switch checkedChildren="开启" unCheckedChildren="关闭" />
                                </Form.Item>
                            </Col>
                        </Row>

                        <Form.Item
                            name="deduplication"
                            label="数据去重"
                            valuePropName="checked"
                            initialValue={true}
                            tooltip="自动过滤重复数据"
                        >
                            <Switch checkedChildren="开启" unCheckedChildren="关闭" />
                        </Form.Item>
                    </Card>
                </Space>
            )
        }
    ]

    return (
        <Form
            form={form}
            layout="vertical"
            onFinish={handleFinish}
            onValuesChange={handleValuesChange}
            initialValues={initialData}
        >
            <Tabs
                activeKey={activeTab}
                onChange={setActiveTab}
                items={tabItems}
            />
        </Form>
    )
}

export default React.memo(AgentProjectForm)
