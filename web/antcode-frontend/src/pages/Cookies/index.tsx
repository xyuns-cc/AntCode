import type React from 'react'
import { useState, useEffect, useMemo, useCallback } from 'react'
import {
    Card,
    Button,
    Space,
    Modal,
    Form,
    Input,
    Select,
    Radio,
    Tag,
    Tooltip,
    Progress,
    Statistic,
    Row,
    Col,
    theme,
    Avatar,
    Badge,
    Typography
} from 'antd'
import {
    PlusOutlined,
    ReloadOutlined,
    DeleteOutlined,
    SearchOutlined,
    DatabaseOutlined,
    CheckCircleOutlined,
    CloseCircleOutlined,
    ClockCircleOutlined,
    KeyOutlined,
    CodeOutlined,
    GlobalOutlined,
    EditOutlined,
    SafetyCertificateOutlined
} from '@ant-design/icons'
import type { ColumnsType } from 'antd/es/table'
import PageContainer from '@/components/common/PageContainer'
import FilterBar from '@/components/common/FilterBar'
import ResponsiveTable from '@/components/common/ResponsiveTable'

const { Text } = Typography
const { TextArea } = Input

interface Account {
    id: number
    name: string
    status: 'online' | 'expired' | 'warning'
    lastCheck: string
    successRate: number
    method: 'cookie' | 'credentials'
    source: string
    scriptType: 'request'
    script: string
}

interface NewAccountForm {
    name: string
    source: string
    method: 'cookie' | 'credentials'
    scriptType: 'request'
    content: string
    username: string
    scriptCode: string
}

const INITIAL_ACCOUNTS: Account[] = []

const Cookies: React.FC = () => {
    const { token } = theme.useToken()
    const [accounts, setAccounts] = useState<Account[]>(INITIAL_ACCOUNTS)
    const [searchTerm, setSearchTerm] = useState('')
    const [updateFrequency, setUpdateFrequency] = useState(300)
    const [timeLeft, setTimeLeft] = useState(300)
    const [isUpdating, setIsUpdating] = useState(false)
    const [showAddModal, setShowAddModal] = useState(false)
    const [form] = Form.useForm<NewAccountForm>()

    const handleAutoRefresh = useCallback(async () => {
        setIsUpdating(true)
        setAccounts(prev => prev)
        setIsUpdating(false)
        setTimeLeft(updateFrequency)
    }, [updateFrequency])

    // 自动刷新计时器
    useEffect(() => {
        const timer = setInterval(() => {
            setTimeLeft((prev) => {
                if (prev <= 1) {
                    void handleAutoRefresh()
                    return updateFrequency
                }
                return prev - 1
            })
        }, 1000)
        return () => clearInterval(timer)
    }, [handleAutoRefresh, updateFrequency])

    const handleAddAccount = async () => {
        try {
            const values = await form.validateFields()
            const account: Account = {
                id: Date.now(),
                name: values.name,
                status: 'online',
                lastCheck: new Date().toLocaleString(),
                successRate: 100,
                method: values.method,
                source: values.source,
                scriptType: values.scriptType,
                script: values.scriptCode
            }
            setAccounts(prev => [account, ...prev])
            setShowAddModal(false)
            form.resetFields()
        } catch {
            // 表单验证失败
        }
    }

    const handleDeleteAccount = (id: number) => {
        setAccounts(prev => prev.filter(acc => acc.id !== id))
    }

    const filteredAccounts = useMemo(() => {
        return accounts.filter(
            acc =>
                acc.name.toLowerCase().includes(searchTerm.toLowerCase()) ||
                acc.source.toLowerCase().includes(searchTerm.toLowerCase())
        )
    }, [accounts, searchTerm])

    const formatTime = (seconds: number) => {
        const m = Math.floor(seconds / 60)
        const s = seconds % 60
        return `${m}:${s < 10 ? '0' : ''}${s}`
    }

    // 统计数据
    const stats = useMemo(() => {
        const total = accounts.length
        const online = accounts.filter(a => a.status === 'online').length
        const expired = accounts.filter(a => a.status === 'expired').length
        const avgSuccessRate = total > 0
            ? Math.round(accounts.reduce((sum, a) => sum + a.successRate, 0) / total)
            : 0
        return { total, online, expired, avgSuccessRate }
    }, [accounts])

    // 状态徽章渲染
    const renderStatusBadge = (status: string) => {
        const configs: Record<string, { label: string; color: string; icon: React.ReactNode }> = {
            online: { label: 'RUNNING', color: 'success', icon: <CheckCircleOutlined /> },
            warning: { label: 'LIMITED', color: 'warning', icon: <ClockCircleOutlined /> },
            expired: { label: 'FAILED', color: 'error', icon: <CloseCircleOutlined /> }
        }
        const config = configs[status] || configs.expired
        return (
            <Tag color={config.color} icon={config.icon}>
                {config.label}
            </Tag>
        )
    }

    // 表格列配置
    const columns: ColumnsType<Account> = [
        {
            title: '账号信息',
            key: 'info',
            width: 200,
            render: (_, record) => (
                <Space>
                    <Avatar style={{ backgroundColor: token.colorPrimary }}>
                        {record.source[0]}
                    </Avatar>
                    <div>
                        <div style={{ fontWeight: 600 }}>{record.name}</div>
                        <Text type="secondary" style={{ fontSize: 12 }}>{record.source}</Text>
                    </div>
                </Space>
            )
        },
        {
            title: '登录模式',
            dataIndex: 'method',
            key: 'method',
            width: 120,
            render: (method: string) => (
                <Tag
                    icon={method === 'cookie' ? <CodeOutlined /> : <KeyOutlined />}
                    color={method === 'cookie' ? 'blue' : 'purple'}
                >
                    {method === 'cookie' ? 'Cookie' : 'Credentials'}
                </Tag>
            )
        },
        {
            title: '脚本引擎',
            key: 'scriptType',
            width: 180,
            render: (_, record) => (
                <div>
                    <Space>
                        <GlobalOutlined style={{ color: token.colorInfo }} />
                        <Text>Request-based</Text>
                    </Space>
                    <div>
                        <Text type="secondary" style={{ fontSize: 10, fontFamily: 'monospace' }} ellipsis>
                            {record.script.substring(0, 30)}...
                        </Text>
                    </div>
                </div>
            )
        },
        {
            title: '运行状态',
            dataIndex: 'status',
            key: 'status',
            width: 120,
            render: (status: string) => renderStatusBadge(status)
        },
        {
            title: '成功率',
            dataIndex: 'successRate',
            key: 'successRate',
            width: 100,
            render: (rate: number) => (
                <Progress
                    percent={rate}
                    size="small"
                    status={rate >= 80 ? 'success' : rate >= 50 ? 'normal' : 'exception'}
                    format={percent => `${percent}%`}
                />
            )
        },
        {
            title: '最近同步',
            dataIndex: 'lastCheck',
            key: 'lastCheck',
            width: 150,
            render: (date: string) => (
                <Text type="secondary" style={{ fontSize: 12 }}>{date}</Text>
            )
        },
        {
            title: '操作',
            key: 'actions',
            width: 100,
            fixed: 'right',
            render: (_, record) => (
                <Space>
                    <Tooltip title="编辑脚本">
                        <Button type="text" icon={<EditOutlined />} />
                    </Tooltip>
                    <Tooltip title="删除">
                        <Button
                            type="text"
                            danger
                            icon={<DeleteOutlined />}
                            onClick={() => handleDeleteAccount(record.id)}
                        />
                    </Tooltip>
                </Space>
            )
        }
    ]

    return (
        <PageContainer
            title={
                <Space>
                    <DatabaseOutlined />
                    <span>Cookie 账号池管理中心</span>
                    <Tag color="orange" style={{ marginLeft: 8 }}>演示中</Tag>
                </Space>
            }
            banner={
                <>
                    {/* O3: 当前只有前端 mock；后端 API/存储未接入。刷新页面即丢，
                        提示用户不要以为已保存。项目级 cookies 走 Rule 项目配置，是另一条链。 */}
                    <div
                        style={{
                            marginBottom: 12,
                            padding: '10px 14px',
                            borderRadius: 6,
                            background: token.colorWarningBg,
                            color: token.colorWarningText,
                            border: `1px solid ${token.colorWarningBorder}`,
                            fontSize: 13,
                        }}
                    >
                        <strong>⚠️ 演示中 · 数据不会保存</strong>
                        <span style={{ marginLeft: 8 }}>
                            账号池后端未接入，此页面仅供 UI 预览；刷新即丢。
                            如需给项目配置固定 Cookie，请在<strong>规则项目</strong>详情里设置。
                        </span>
                    </div>
                    <Row gutter={12}>
                        <Col span={6}>
                            <Card><Statistic title="总账号数" value={stats.total} prefix={<DatabaseOutlined />} valueStyle={{ color: token.colorPrimary }} /></Card>
                        </Col>
                        <Col span={6}>
                            <Card><Statistic title="运行中" value={stats.online} prefix={<CheckCircleOutlined />} valueStyle={{ color: token.colorSuccess }} /></Card>
                        </Col>
                        <Col span={6}>
                            <Card><Statistic title="已失效" value={stats.expired} prefix={<CloseCircleOutlined />} valueStyle={{ color: token.colorError }} /></Card>
                        </Col>
                        <Col span={6}>
                            <Card><Statistic title="平均成功率" value={stats.avgSuccessRate} suffix="%" prefix={<SafetyCertificateOutlined />} valueStyle={{ color: stats.avgSuccessRate >= 80 ? token.colorSuccess : token.colorWarning }} /></Card>
                        </Col>
                    </Row>
                </>
            }
            toolbar={
                <FilterBar
                    filters={
                        <Input
                            placeholder="搜索账号标识、平台名称..."
                            prefix={<SearchOutlined />}
                            allowClear
                            value={searchTerm}
                            onChange={(e) => setSearchTerm(e.target.value)}
                            style={{ width: 280 }}
                        />
                    }
                    actions={
                        <>
                            <Badge count={formatTime(timeLeft)} color={token.colorPrimary}>
                                <Button icon={<ReloadOutlined spin={isUpdating} />} onClick={handleAutoRefresh} loading={isUpdating}>
                                    {isUpdating ? '正在同步...' : '立即同步'}
                                </Button>
                            </Badge>
                            <Select
                                value={updateFrequency}
                                onChange={(value) => {
                                    setUpdateFrequency(value)
                                    setTimeLeft(value)
                                }}
                                style={{ width: 130 }}
                                options={[
                                    { label: '1分钟检查', value: 60 },
                                    { label: '5分钟检查', value: 300 },
                                    { label: '1小时检查', value: 3600 }
                                ]}
                            />
                            <Button type="primary" icon={<PlusOutlined />} onClick={() => setShowAddModal(true)}>
                                接入新账号
                            </Button>
                        </>
                    }
                />
            }
        >
            <ResponsiveTable<Account>
                fill
                columns={columns}
                dataSource={filteredAccounts}
                rowKey="id"
                pagination={{}}
            />

            {/* 添加账号弹窗 */}
            <Modal
                title={
                    <Space>
                        <PlusOutlined />
                        <span>接入新账号与脚本</span>
                    </Space>
                }
                open={showAddModal}
                onCancel={() => {
                    setShowAddModal(false)
                    form.resetFields()
                }}
                onOk={handleAddAccount}
                okText="保存并启动"
                cancelText="取消"
                width={700}
                destroyOnClose
            >
                <Form
                    form={form}
                    layout="vertical"
                    initialValues={{
                        method: 'cookie',
                        scriptType: 'request',
                        source: '',
                        scriptCode: '// 输入您的自动化脚本逻辑...'
                    }}
                >
                    <Row gutter={16}>
                        <Col span={12}>
                            <Form.Item
                                label="账号标识名"
                                name="name"
                                rules={[{ required: true, message: '请输入账号标识名' }]}
                            >
                                <Input placeholder="例如: crawler-account-01" />
                            </Form.Item>
                        </Col>
                        <Col span={12}>
                            <Form.Item
                                label="所属平台"
                                name="source"
                                rules={[{ required: true, message: '请输入所属平台' }]}
                            >
                                <Input placeholder="例如: Weibo, TikTok" />
                            </Form.Item>
                        </Col>
                    </Row>

                    <Form.Item label="验证模式" name="method">
                        <Radio.Group buttonStyle="solid">
                            <Radio.Button value="cookie">
                                <CodeOutlined /> Cookie 导入
                            </Radio.Button>
                            <Radio.Button value="credentials">
                                <KeyOutlined /> 账号密码
                            </Radio.Button>
                        </Radio.Group>
                    </Form.Item>

                    <Form.Item
                        noStyle
                        shouldUpdate={(prevValues, currentValues) => prevValues.method !== currentValues.method}
                    >
                        {({ getFieldValue }) =>
                            getFieldValue('method') === 'cookie' ? (
                                <Form.Item
                                    label="Cookie 数据"
                                    name="content"
                                    rules={[{ required: true, message: '请输入 Cookie 数据' }]}
                                >
                                    <TextArea
                                        rows={3}
                                        placeholder="粘贴 JSON 或 Raw Cookie 字符串..."
                                        style={{ fontFamily: 'monospace' }}
                                    />
                                </Form.Item>
                            ) : (
                                <Row gutter={16}>
                                    <Col span={12}>
                                        <Form.Item
                                            label="用户名 / 手机"
                                            name="username"
                                            rules={[{ required: true, message: '请输入用户名' }]}
                                        >
                                            <Input placeholder="请输入用户名或手机号" />
                                        </Form.Item>
                                    </Col>
                                    <Col span={12}>
                                        <Form.Item
                                            label="密码"
                                            name="content"
                                            rules={[{ required: true, message: '请输入密码' }]}
                                        >
                                            <Input.Password placeholder="请输入密码" />
                                        </Form.Item>
                                    </Col>
                                </Row>
                            )
                        }
                    </Form.Item>

                    <Card
                        size="small"
                        style={{ background: token.colorBgLayout }}
                        title={
                            <Space>
                                <CodeOutlined />
                                <span>自定义登录脚本</span>
                            </Space>
                        }
                        extra={
                            <Form.Item name="scriptType" noStyle>
                                <Select
                                    size="small"
                                    style={{ width: 140 }}
                                    options={[
                                        { label: 'Request Engine', value: 'request' }
                                    ]}
                                />
                            </Form.Item>
                        }
                    >
                        <Form.Item
                            noStyle
                            shouldUpdate={(prevValues, currentValues) => prevValues.scriptType !== currentValues.scriptType}
                        >
                            {() => (
                                <Text type="secondary" style={{ fontSize: 12, display: 'block', marginBottom: 12 }}>
                                    使用 Request 登录请求与响应校验配置执行真实登录验证，不提供绕过真实校验的成功路径。
                                </Text>
                            )}
                        </Form.Item>
                        <Form.Item name="scriptCode" noStyle>
                            <TextArea
                                rows={4}
                                style={{ fontFamily: 'monospace', fontSize: 12 }}
                                placeholder="// 输入您的自动化脚本逻辑..."
                            />
                        </Form.Item>
                    </Card>
                </Form>
            </Modal>
        </PageContainer>
    )
}

export default Cookies
