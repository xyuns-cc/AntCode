import type React from 'react'
import { useState, useEffect, useMemo } from 'react'
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
    DesktopOutlined,
    EditOutlined,
    SafetyCertificateOutlined
} from '@ant-design/icons'
import type { ColumnsType } from 'antd/es/table'
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
    scriptType: 'request' | 'browser'
    script: string
}

interface NewAccountForm {
    name: string
    source: string
    method: 'cookie' | 'credentials'
    scriptType: 'request' | 'browser'
    content: string
    username: string
    scriptCode: string
}

// 初始模拟数据
const INITIAL_ACCOUNTS: Account[] = [
    {
        id: 1,
        name: 'Premium_User_01',
        status: 'online',
        lastCheck: '2024-03-20 10:30',
        successRate: 98,
        method: 'cookie',
        source: 'Weibo',
        scriptType: 'request',
        script: "fetch('api/login', { method: 'POST', body: JSON.stringify({cookie: val}) })"
    },
    {
        id: 2,
        name: 'Crawler_Node_A',
        status: 'online',
        lastCheck: '2024-03-20 10:28',
        successRate: 95,
        method: 'credentials',
        source: 'TikTok',
        scriptType: 'browser',
        script: "await page.goto('login.html'); await page.type('#user', user); await page.click('#submit');"
    },
    {
        id: 3,
        name: 'Test_Account_X',
        status: 'expired',
        lastCheck: '2024-03-19 15:45',
        successRate: 42,
        method: 'cookie',
        source: 'Bilibili',
        scriptType: 'request',
        script: '// Auto-refresh logic'
    },
    {
        id: 4,
        name: 'API_Service_Bot',
        status: 'online',
        lastCheck: '2024-03-20 10:32',
        successRate: 100,
        method: 'credentials',
        source: 'YouTube',
        scriptType: 'browser',
        script: "await page.authenticate({username, password});"
    },
    {
        id: 5,
        name: 'Data_Collector_02',
        status: 'warning',
        lastCheck: '2024-03-20 09:15',
        successRate: 76,
        method: 'cookie',
        source: 'Douyin',
        scriptType: 'request',
        script: 'headers.set("Cookie", cookieValue);'
    }
]

const Cookies: React.FC = () => {
    const { token } = theme.useToken()
    const [accounts, setAccounts] = useState<Account[]>(INITIAL_ACCOUNTS)
    const [searchTerm, setSearchTerm] = useState('')
    const [updateFrequency, setUpdateFrequency] = useState(300)
    const [timeLeft, setTimeLeft] = useState(300)
    const [isUpdating, setIsUpdating] = useState(false)
    const [showAddModal, setShowAddModal] = useState(false)
    const [form] = Form.useForm<NewAccountForm>()

    // 自动刷新计时器
    useEffect(() => {
        const timer = setInterval(() => {
            setTimeLeft((prev) => {
                if (prev <= 1) {
                    handleAutoRefresh()
                    return updateFrequency
                }
                return prev - 1
            })
        }, 1000)
        return () => clearInterval(timer)
    }, [updateFrequency])

    const handleAutoRefresh = async () => {
        setIsUpdating(true)
        await new Promise(resolve => setTimeout(resolve, 2000))
        setAccounts(prev =>
            prev.map(acc => ({
                ...acc,
                lastCheck: new Date().toLocaleString(),
                status: Math.random() > 0.15 ? 'online' : 'expired'
            }))
        )
        setIsUpdating(false)
        setTimeLeft(updateFrequency)
    }

    const handleAddAccount = async () => {
        try {
            const values = await form.validateFields()
            const account: Account = {
                id: Date.now(),
                name: values.name || `User_${Math.floor(Math.random() * 1000)}`,
                status: 'online',
                lastCheck: new Date().toLocaleString(),
                successRate: 100,
                method: values.method,
                source: values.source,
                scriptType: values.scriptType,
                script: values.scriptCode
            }
            setAccounts([account, ...accounts])
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
                        {record.scriptType === 'request' ? (
                            <GlobalOutlined style={{ color: token.colorInfo }} />
                        ) : (
                            <DesktopOutlined style={{ color: token.colorWarning }} />
                        )}
                        <Text>{record.scriptType === 'request' ? 'Request-based' : 'Browser-based'}</Text>
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
        <div style={{ padding: '24px' }}>
            {/* 页面标题 */}
            <div style={{ marginBottom: '24px' }}>
                <h1 style={{ fontSize: '24px', fontWeight: 'bold', margin: 0, display: 'flex', alignItems: 'center', gap: '8px' }}>
                    <DatabaseOutlined />
                    Cookie 账号池管理中心
                </h1>
                <p style={{ margin: '8px 0 0 0', opacity: 0.65 }}>
                    自动化会话管理与登录脚本配置
                </p>
            </div>

            {/* 统计卡片 */}
            <Row gutter={16} style={{ marginBottom: 24 }}>
                <Col span={6}>
                    <Card>
                        <Statistic
                            title="总账号数"
                            value={stats.total}
                            prefix={<DatabaseOutlined />}
                            valueStyle={{ color: token.colorPrimary }}
                        />
                    </Card>
                </Col>
                <Col span={6}>
                    <Card>
                        <Statistic
                            title="运行中"
                            value={stats.online}
                            prefix={<CheckCircleOutlined />}
                            valueStyle={{ color: token.colorSuccess }}
                        />
                    </Card>
                </Col>
                <Col span={6}>
                    <Card>
                        <Statistic
                            title="已失效"
                            value={stats.expired}
                            prefix={<CloseCircleOutlined />}
                            valueStyle={{ color: token.colorError }}
                        />
                    </Card>
                </Col>
                <Col span={6}>
                    <Card>
                        <Statistic
                            title="平均成功率"
                            value={stats.avgSuccessRate}
                            suffix="%"
                            prefix={<SafetyCertificateOutlined />}
                            valueStyle={{ color: stats.avgSuccessRate >= 80 ? token.colorSuccess : token.colorWarning }}
                        />
                    </Card>
                </Col>
            </Row>

            {/* 工具栏 */}
            <Card style={{ marginBottom: 16 }}>
                <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', flexWrap: 'wrap', gap: '12px' }}>
                    <Space wrap size="middle">
                        <Badge count={formatTime(timeLeft)} color={token.colorPrimary}>
                            <Button
                                icon={<ReloadOutlined spin={isUpdating} />}
                                onClick={handleAutoRefresh}
                                loading={isUpdating}
                            >
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
                        <Button
                            type="primary"
                            icon={<PlusOutlined />}
                            onClick={() => setShowAddModal(true)}
                        >
                            接入新账号
                        </Button>
                    </Space>
                    <Input
                        placeholder="搜索账号标识、平台名称..."
                        prefix={<SearchOutlined />}
                        allowClear
                        value={searchTerm}
                        onChange={(e) => setSearchTerm(e.target.value)}
                        style={{ width: 280 }}
                    />
                </div>
            </Card>

            {/* 账号表格 */}
            <Card>
                <ResponsiveTable<Account>
                    columns={columns}
                    dataSource={filteredAccounts}
                    rowKey="id"
                    pagination={{
                        showSizeChanger: true,
                        showQuickJumper: true,
                        showTotal: (total, range) => `第 ${range[0]}-${range[1]} 条，共 ${total} 条`
                    }}
                />
            </Card>

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
                                <Input placeholder="例如: Crawler_Node_01" />
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
                                        { label: 'Request Engine', value: 'request' },
                                        { label: 'Browser Engine', value: 'browser' }
                                    ]}
                                />
                            </Form.Item>
                        }
                    >
                        <Form.Item
                            noStyle
                            shouldUpdate={(prevValues, currentValues) => prevValues.scriptType !== currentValues.scriptType}
                        >
                            {({ getFieldValue }) => (
                                <Text type="secondary" style={{ fontSize: 12, display: 'block', marginBottom: 12 }}>
                                    {getFieldValue('scriptType') === 'request'
                                        ? '使用内置 Request 库模拟 HTTP 协议登录，性能极高，资源占用低。'
                                        : '启动 Headless 浏览器执行 DOM 操作登录，适用于复杂滑块和加密逻辑。'}
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
        </div>
    )
}

export default Cookies
