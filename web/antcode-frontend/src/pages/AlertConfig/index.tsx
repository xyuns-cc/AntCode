/**
 * 告警配置页面
 */
import React, { useState, useEffect, memo } from 'react'
import {
  Card,
  Tabs,
  Form,
  Input,
  Button,
  Space,
  Switch,
  InputNumber,
  Select,
  Table,
  Tag,
  Popconfirm,
  Modal,
  Spin,
  Empty,
  Statistic,
  Row,
  Col,
  Divider,
  Alert
} from 'antd'
import {
  BellOutlined,
  PlusOutlined,
  DeleteOutlined,
  EditOutlined,
  SendOutlined,
  ReloadOutlined,
  HistoryOutlined,
  BarChartOutlined,
  CheckCircleOutlined,
  CloseCircleOutlined
} from '@ant-design/icons'
import { useAuthStore } from '@/stores/authStore'
import { alertService } from '@/services/alert'
import type {
  AlertConfigResponse,
  WebhookConfig,
  AlertHistoryItem,
  AlertStatsResponse,
  EmailConfig,
  EmailRecipient
} from '@/services/alert'
import showNotification from '@/utils/notification'
import styles from './AlertConfig.module.css'

const { Option } = Select

// 告警级别选项
const ALERT_LEVELS = ['DEBUG', 'INFO', 'WARNING', 'ERROR', 'CRITICAL']

// 级别颜色映射
const LEVEL_COLORS: Record<string, string> = {
  DEBUG: 'default',
  INFO: 'blue',
  WARNING: 'orange',
  ERROR: 'red',
  CRITICAL: 'purple'
}

// 渠道类型映射
const CHANNEL_NAMES: Record<string, string> = {
  feishu: '飞书',
  dingtalk: '钉钉',
  wecom: '企业微信',
  email: '邮件'
}

const AlertConfig: React.FC = memo(() => {
  const { user } = useAuthStore()
  const [loading, setLoading] = useState(false)
  const [config, setConfig] = useState<AlertConfigResponse | null>(null)
  const [history, setHistory] = useState<AlertHistoryItem[]>([])
  const [stats, setStats] = useState<AlertStatsResponse | null>(null)
  const [historyLoading, setHistoryLoading] = useState(false)
  const [statsLoading, setStatsLoading] = useState(false)
  
  // 表单
  const [configForm] = Form.useForm()
  const [webhookForm] = Form.useForm()
  const [emailForm] = Form.useForm()
  const [recipientForm] = Form.useForm()
  
  // 弹窗状态
  const [webhookModalVisible, setWebhookModalVisible] = useState(false)
  const [editingWebhook, setEditingWebhook] = useState<{
    type: 'feishu' | 'dingtalk' | 'wecom'
    index: number
    data?: WebhookConfig
  } | null>(null)
  
  // 邮件配置弹窗
  const [emailModalVisible, setEmailModalVisible] = useState(false)
  const [recipientModalVisible, setRecipientModalVisible] = useState(false)
  const [editingRecipient, setEditingRecipient] = useState<{ index: number; data?: EmailRecipient } | null>(null)
  
  // 测试状态
  const [testLoading, setTestLoading] = useState<string | null>(null)

  // 检查权限
  const isSuperAdmin = user?.username === 'admin'

  // 加载配置
  const loadConfig = async () => {
    setLoading(true)
    try {
      const data = await alertService.getConfig()
      setConfig(data)
      
      // 设置表单值
      configForm.setFieldsValue({
        auto_alert_levels: data.auto_alert_levels,
        rate_limit_enabled: data.rate_limit.enabled,
        rate_limit_window: data.rate_limit.window,
        rate_limit_max_count: data.rate_limit.max_count,
        retry_enabled: data.retry.enabled,
        max_retries: data.retry.max_retries,
        retry_delay: data.retry.retry_delay
      })
    } catch (error: unknown) {
      const errMsg = error instanceof Error ? error.message : '未知错误'
      showNotification('error', '加载告警配置失败', errMsg)
    } finally {
      setLoading(false)
    }
  }

  // 加载历史
  const loadHistory = async () => {
    setHistoryLoading(true)
    try {
      const data = await alertService.getHistory({ limit: 100 })
      setHistory(data.items)
    } catch (error: unknown) {
      const errMsg = error instanceof Error ? error.message : '未知错误'
      showNotification('error', '加载告警历史失败', errMsg)
    } finally {
      setHistoryLoading(false)
    }
  }

  // 加载统计
  const loadStats = async () => {
    setStatsLoading(true)
    try {
      const data = await alertService.getStats()
      setStats(data)
    } catch (error: unknown) {
      const errMsg = error instanceof Error ? error.message : '未知错误'
      showNotification('error', '加载告警统计失败', errMsg)
    } finally {
      setStatsLoading(false)
    }
  }

  useEffect(() => {
    loadConfig()
    loadHistory()
    loadStats()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  // 保存配置
  const handleSaveConfig = async () => {
    if (!isSuperAdmin) {
      showNotification('error', '权限不足', '仅超级管理员可修改配置')
      return
    }
    
    try {
      const values = await configForm.validateFields()
      
      await alertService.updateConfig({
        auto_alert_levels: values.auto_alert_levels,
        rate_limit: {
          enabled: values.rate_limit_enabled,
          window: values.rate_limit_window,
          max_count: values.rate_limit_max_count
        },
        retry: {
          enabled: values.retry_enabled,
          max_retries: values.max_retries,
          retry_delay: values.retry_delay
        }
      })
      
      showNotification('success', '告警配置已保存')
      loadConfig()
    } catch (error: unknown) {
      const errMsg = error instanceof Error ? error.message : '未知错误'
      showNotification('error', '保存配置失败', errMsg)
    }
  }

  // 添加/编辑 Webhook
  const handleWebhookSubmit = async () => {
    if (!isSuperAdmin || !editingWebhook) return
    
    try {
      const values = await webhookForm.validateFields()
      const webhookData: WebhookConfig = {
        name: values.name,
        url: values.url,
        levels: values.levels,
        enabled: values.enabled ?? true
      }
      
      // 更新配置
      const newConfig = { ...config! }
      const webhookKey = `${editingWebhook.type}_webhooks` as 'feishu_webhooks' | 'dingtalk_webhooks' | 'wecom_webhooks'
      const webhooks = [...(newConfig.channels[webhookKey] as WebhookConfig[])]
      
      if (editingWebhook.index >= 0) {
        webhooks[editingWebhook.index] = webhookData
      } else {
        webhooks.push(webhookData)
      }
      
      await alertService.updateConfig({
        channels: {
          ...newConfig.channels,
          [webhookKey]: webhooks
        }
      })
      
      showNotification('success', editingWebhook.index >= 0 ? 'Webhook 已更新' : 'Webhook 已添加')
      setWebhookModalVisible(false)
      setEditingWebhook(null)
      webhookForm.resetFields()
      loadConfig()
    } catch (error: unknown) {
      const errMsg = error instanceof Error ? error.message : '未知错误'
      showNotification('error', '操作失败', errMsg)
    }
  }

  // 删除 Webhook
  const handleDeleteWebhook = async (type: 'feishu' | 'dingtalk' | 'wecom', index: number) => {
    if (!isSuperAdmin || !config) return
    
    try {
      const webhookKey = `${type}_webhooks` as 'feishu_webhooks' | 'dingtalk_webhooks' | 'wecom_webhooks'
      const webhooks = [...(config.channels[webhookKey] as WebhookConfig[])]
      webhooks.splice(index, 1)
      
      await alertService.updateConfig({
        channels: {
          ...config.channels,
          [webhookKey]: webhooks
        }
      })
      
      showNotification('success', 'Webhook 已删除')
      loadConfig()
    } catch (error: unknown) {
      const errMsg = error instanceof Error ? error.message : '未知错误'
      showNotification('error', '删除失败', errMsg)
    }
  }

  // 测试告警
  const handleTestAlert = async (channel: string) => {
    setTestLoading(channel)
    try {
      const result = await alertService.sendTestAlert({ channel })
      if (result.success) {
        showNotification('success', '测试告警发送成功', result.message)
      } else {
        showNotification('error', '测试告警发送失败', result.message)
      }
      loadHistory()
    } catch (error: unknown) {
      const errMsg = error instanceof Error ? error.message : '未知错误'
      showNotification('error', '测试失败', errMsg)
    } finally {
      setTestLoading(null)
    }
  }

  // 重新加载配置
  const handleReloadConfig = async () => {
    try {
      await alertService.reloadConfig()
      showNotification('success', '告警配置已重新加载')
      loadConfig()
    } catch (error: unknown) {
      const errMsg = error instanceof Error ? error.message : '未知错误'
      showNotification('error', '重新加载失败', errMsg)
    }
  }

  // 打开添加 Webhook 弹窗
  const openAddWebhookModal = (type: 'feishu' | 'dingtalk' | 'wecom') => {
    setEditingWebhook({ type, index: -1 })
    webhookForm.resetFields()
    webhookForm.setFieldsValue({
      levels: ['ERROR', 'CRITICAL'],
      enabled: true
    })
    setWebhookModalVisible(true)
  }

  // 打开编辑 Webhook 弹窗
  const openEditWebhookModal = (type: 'feishu' | 'dingtalk' | 'wecom', index: number, data: WebhookConfig) => {
    setEditingWebhook({ type, index, data })
    webhookForm.setFieldsValue(data)
    setWebhookModalVisible(true)
  }

  // 打开邮件配置弹窗
  const openEmailConfigModal = () => {
    if (config?.channels.email_config) {
      emailForm.setFieldsValue(config.channels.email_config)
    } else {
      emailForm.resetFields()
      emailForm.setFieldsValue({
        smtp_port: 465,
        smtp_ssl: true,
        sender_name: 'AntCode告警系统',
        recipients: []
      })
    }
    setEmailModalVisible(true)
  }

  // 保存邮件配置
  const handleEmailConfigSubmit = async () => {
    if (!isSuperAdmin) return
    
    try {
      const values = await emailForm.validateFields()
      const emailConfig: EmailConfig = {
        smtp_host: values.smtp_host || '',
        smtp_port: values.smtp_port || 465,
        smtp_user: values.smtp_user || '',
        smtp_password: values.smtp_password || '',
        smtp_ssl: values.smtp_ssl ?? true,
        sender_name: values.sender_name || 'AntCode告警系统',
        recipients: config?.channels.email_config?.recipients || []
      }
      
      await alertService.updateConfig({
        channels: {
          ...config!.channels,
          email_config: emailConfig
        }
      })
      
      showNotification('success', '邮件配置已保存')
      setEmailModalVisible(false)
      loadConfig()
    } catch (error: unknown) {
      const errMsg = error instanceof Error ? error.message : '未知错误'
      showNotification('error', '保存失败', errMsg)
    }
  }

  // 打开添加收件人弹窗
  const openAddRecipientModal = () => {
    setEditingRecipient({ index: -1 })
    recipientForm.resetFields()
    recipientForm.setFieldsValue({
      levels: ['ERROR', 'CRITICAL']
    })
    setRecipientModalVisible(true)
  }

  // 打开编辑收件人弹窗
  const openEditRecipientModal = (index: number, data: EmailRecipient) => {
    setEditingRecipient({ index, data })
    recipientForm.setFieldsValue(data)
    setRecipientModalVisible(true)
  }

  // 保存收件人
  const handleRecipientSubmit = async () => {
    if (!isSuperAdmin || !config?.channels.email_config) return
    
    try {
      const values = await recipientForm.validateFields()
      const recipient: EmailRecipient = {
        email: values.email,
        name: values.name || '',
        levels: values.levels || ['ERROR', 'CRITICAL']
      }
      
      const recipients = [...(config.channels.email_config.recipients || [])]
      if (editingRecipient && editingRecipient.index >= 0) {
        recipients[editingRecipient.index] = recipient
      } else {
        recipients.push(recipient)
      }
      
      await alertService.updateConfig({
        channels: {
          ...config.channels,
          email_config: {
            ...config.channels.email_config,
            recipients
          }
        }
      })
      
      showNotification('success', editingRecipient?.index === -1 ? '收件人已添加' : '收件人已更新')
      setRecipientModalVisible(false)
      setEditingRecipient(null)
      recipientForm.resetFields()
      loadConfig()
    } catch (error: unknown) {
      const errMsg = error instanceof Error ? error.message : '未知错误'
      showNotification('error', '操作失败', errMsg)
    }
  }

  // 删除收件人
  const handleDeleteRecipient = async (index: number) => {
    if (!isSuperAdmin || !config?.channels.email_config) return
    
    try {
      const recipients = [...config.channels.email_config.recipients]
      recipients.splice(index, 1)
      
      await alertService.updateConfig({
        channels: {
          ...config.channels,
          email_config: {
            ...config.channels.email_config,
            recipients
          }
        }
      })
      
      showNotification('success', '收件人已删除')
      loadConfig()
    } catch (error: unknown) {
      const errMsg = error instanceof Error ? error.message : '未知错误'
      showNotification('error', '删除失败', errMsg)
    }
  }

  // 渲染邮件配置卡片
  const renderEmailConfig = () => {
    if (!config) return null
    
    const emailConfig = config.channels.email_config
    const hasConfig = emailConfig && emailConfig.smtp_host
    
    return (
      <Card
        title="邮件告警"
        size="small"
        extra={
          isSuperAdmin && (
            <Button
              type="primary"
              size="small"
              icon={<EditOutlined />}
              onClick={openEmailConfigModal}
            >
              {hasConfig ? '编辑' : '配置'}
            </Button>
          )
        }
        className={styles.webhookCard}
      >
        {!hasConfig ? (
          <Empty description="暂未配置SMTP" image={Empty.PRESENTED_IMAGE_SIMPLE} />
        ) : (
          <>
            <div style={{ marginBottom: 12 }}>
              <Tag color="blue">SMTP: {emailConfig.smtp_host}:{emailConfig.smtp_port}</Tag>
              <Tag color={emailConfig.smtp_ssl ? 'green' : 'default'}>
                {emailConfig.smtp_ssl ? 'SSL' : '非SSL'}
              </Tag>
            </div>
            <Divider style={{ margin: '8px 0' }}>收件人 ({emailConfig.recipients?.length || 0})</Divider>
            {(!emailConfig.recipients || emailConfig.recipients.length === 0) ? (
              <Empty description="暂无收件人" image={Empty.PRESENTED_IMAGE_SIMPLE} />
            ) : (
              <div className={styles.webhookList}>
                {emailConfig.recipients.map((recipient, index) => (
                  <div key={index} className={styles.webhookItem}>
                    <div className={styles.webhookInfo}>
                      <div className={styles.webhookName}>
                        {recipient.name || recipient.email}
                      </div>
                      <div className={styles.webhookUrl}>{recipient.email}</div>
                      <div className={styles.webhookLevels}>
                        {recipient.levels?.map(level => (
                          <Tag key={level} color={LEVEL_COLORS[level]}>{level}</Tag>
                        ))}
                      </div>
                    </div>
                    {isSuperAdmin && (
                      <Space>
                        <Button
                          size="small"
                          icon={<EditOutlined />}
                          onClick={() => openEditRecipientModal(index, recipient)}
                        />
                        <Popconfirm
                          title="确定删除此收件人？"
                          onConfirm={() => handleDeleteRecipient(index)}
                        >
                          <Button size="small" danger icon={<DeleteOutlined />} />
                        </Popconfirm>
                      </Space>
                    )}
                  </div>
                ))}
              </div>
            )}
            {isSuperAdmin && hasConfig && (
              <>
                <Divider style={{ margin: '12px 0' }} />
                <Space>
                  <Button
                    size="small"
                    icon={<PlusOutlined />}
                    onClick={openAddRecipientModal}
                  >
                    添加收件人
                  </Button>
                  <Button
                    size="small"
                    icon={<SendOutlined />}
                    loading={testLoading === 'email'}
                    onClick={() => handleTestAlert('email')}
                  >
                    发送测试
                  </Button>
                </Space>
              </>
            )}
          </>
        )}
      </Card>
    )
  }

  // 渲染 Webhook 列表
  const renderWebhookList = (type: 'feishu' | 'dingtalk' | 'wecom') => {
    if (!config) return null
    
    const webhookKey = `${type}_webhooks` as 'feishu_webhooks' | 'dingtalk_webhooks' | 'wecom_webhooks'
    const webhooks = config.channels[webhookKey] as WebhookConfig[]
    
    return (
      <Card
        title={`${CHANNEL_NAMES[type]} Webhook`}
        size="small"
        extra={
          isSuperAdmin && (
            <Button
              type="primary"
              size="small"
              icon={<PlusOutlined />}
              onClick={() => openAddWebhookModal(type)}
            >
              添加
            </Button>
          )
        }
        className={styles.webhookCard}
      >
        {webhooks.length === 0 ? (
          <Empty description="暂无配置" image={Empty.PRESENTED_IMAGE_SIMPLE} />
        ) : (
          <div className={styles.webhookList}>
            {webhooks.map((webhook, index) => (
              <div key={index} className={styles.webhookItem}>
                <div className={styles.webhookInfo}>
                  <div className={styles.webhookName}>
                    {webhook.name}
                    {webhook.enabled ? (
                      <Tag color="green" style={{ marginLeft: 8 }}>启用</Tag>
                    ) : (
                      <Tag color="default" style={{ marginLeft: 8 }}>禁用</Tag>
                    )}
                  </div>
                  <div className={styles.webhookUrl}>{webhook.url}</div>
                  <div className={styles.webhookLevels}>
                    {webhook.levels.map(level => (
                      <Tag key={level} color={LEVEL_COLORS[level]}>{level}</Tag>
                    ))}
                  </div>
                </div>
                {isSuperAdmin && (
                  <Space>
                    <Button
                      size="small"
                      icon={<EditOutlined />}
                      onClick={() => openEditWebhookModal(type, index, webhook)}
                    />
                    <Popconfirm
                      title="确定删除此 Webhook？"
                      onConfirm={() => handleDeleteWebhook(type, index)}
                    >
                      <Button size="small" danger icon={<DeleteOutlined />} />
                    </Popconfirm>
                  </Space>
                )}
              </div>
            ))}
          </div>
        )}
        <Divider style={{ margin: '12px 0' }} />
        <Button
          size="small"
          icon={<SendOutlined />}
          loading={testLoading === type}
          onClick={() => handleTestAlert(type)}
        >
          发送测试
        </Button>
      </Card>
    )
  }

  // 历史记录表格列
  const historyColumns = [
    {
      title: '时间',
      dataIndex: 'timestamp',
      key: 'timestamp',
      width: 180
    },
    {
      title: '级别',
      dataIndex: 'level',
      key: 'level',
      width: 100,
      render: (level: string) => (
        <Tag color={LEVEL_COLORS[level]}>{level}</Tag>
      )
    },
    {
      title: '来源',
      dataIndex: 'source',
      key: 'source',
      width: 100
    },
    {
      title: '消息',
      dataIndex: 'message',
      key: 'message',
      ellipsis: true
    },
    {
      title: '状态',
      dataIndex: 'status',
      key: 'status',
      width: 100,
      render: (status: string) => (
        status === 'sent' ? (
          <Tag icon={<CheckCircleOutlined />} color="success">已发送</Tag>
        ) : (
          <Tag icon={<CloseCircleOutlined />} color="error">失败</Tag>
        )
      )
    }
  ]

  if (loading && !config) {
    return (
      <div className={styles.loadingContainer}>
        <Spin size="large" />
      </div>
    )
  }

  return (
    <div className={styles.alertConfigContainer}>
      <div className={styles.pageHeader}>
        <h1 className={styles.pageTitle}>
          <BellOutlined style={{ marginRight: 8 }} />
          告警配置
        </h1>
        <Space>
          <Button icon={<ReloadOutlined />} onClick={handleReloadConfig}>
            重新加载
          </Button>
          <Button
            type="primary"
            icon={<SendOutlined />}
            loading={testLoading === 'all'}
            onClick={() => handleTestAlert('all')}
          >
            测试所有渠道
          </Button>
        </Space>
      </div>

      {!isSuperAdmin && (
        <Alert
          message="权限提示"
          description="您当前为管理员，可查看告警配置。修改配置需要超级管理员权限。"
          type="info"
          showIcon
          style={{ marginBottom: 16 }}
        />
      )}

      <Tabs
        defaultActiveKey="channels"
        items={[
          {
            key: 'channels',
            label: (
              <span>
                <BellOutlined />
                告警渠道
              </span>
            ),
            children: (
              <>
                <Row gutter={16} style={{ marginBottom: 16 }}>
                  <Col span={8}>{renderWebhookList('feishu')}</Col>
                  <Col span={8}>{renderWebhookList('dingtalk')}</Col>
                  <Col span={8}>{renderWebhookList('wecom')}</Col>
                </Row>
                <Row gutter={16}>
                  <Col span={8}>{renderEmailConfig()}</Col>
                </Row>
              </>
            )
          },
          {
            key: 'settings',
            label: (
              <span>
                <EditOutlined />
                告警设置
              </span>
            ),
            children: (
              <Card title="告警设置" size="small">
                <Form
                  form={configForm}
                  layout="vertical"
                  onFinish={handleSaveConfig}
                >
                  <Form.Item
                    label="自动告警级别"
                    name="auto_alert_levels"
                    tooltip="选择哪些级别的日志会自动触发告警"
                  >
                    <Select mode="multiple" placeholder="选择告警级别">
                      {ALERT_LEVELS.map(level => (
                        <Option key={level} value={level}>
                          <Tag color={LEVEL_COLORS[level]}>{level}</Tag>
                        </Option>
                      ))}
                    </Select>
                  </Form.Item>

                  <Divider>限流配置</Divider>

                  <Row gutter={16}>
                    <Col span={8}>
                      <Form.Item
                        label="启用限流"
                        name="rate_limit_enabled"
                        valuePropName="checked"
                      >
                        <Switch />
                      </Form.Item>
                    </Col>
                    <Col span={8}>
                      <Form.Item
                        label="限流窗口（秒）"
                        name="rate_limit_window"
                        rules={[{ required: true }]}
                      >
                        <InputNumber min={10} max={3600} style={{ width: '100%' }} />
                      </Form.Item>
                    </Col>
                    <Col span={8}>
                      <Form.Item
                        label="窗口内最大告警数"
                        name="rate_limit_max_count"
                        rules={[{ required: true }]}
                      >
                        <InputNumber min={1} max={100} style={{ width: '100%' }} />
                      </Form.Item>
                    </Col>
                  </Row>

                  <Divider>重试配置</Divider>

                  <Row gutter={16}>
                    <Col span={8}>
                      <Form.Item
                        label="启用重试"
                        name="retry_enabled"
                        valuePropName="checked"
                      >
                        <Switch />
                      </Form.Item>
                    </Col>
                    <Col span={8}>
                      <Form.Item
                        label="最大重试次数"
                        name="max_retries"
                        rules={[{ required: true }]}
                      >
                        <InputNumber min={1} max={10} style={{ width: '100%' }} />
                      </Form.Item>
                    </Col>
                    <Col span={8}>
                      <Form.Item
                        label="重试间隔（秒）"
                        name="retry_delay"
                        rules={[{ required: true }]}
                      >
                        <InputNumber min={0.1} max={60} step={0.1} style={{ width: '100%' }} />
                      </Form.Item>
                    </Col>
                  </Row>

                  {isSuperAdmin && (
                    <Form.Item>
                      <Button type="primary" htmlType="submit">
                        保存配置
                      </Button>
                    </Form.Item>
                  )}
                </Form>
              </Card>
            )
          },
          {
            key: 'history',
            label: (
              <span>
                <HistoryOutlined />
                告警历史
              </span>
            ),
            children: (
              <Card
                title="告警历史"
                size="small"
                extra={
                  <Button
                    icon={<ReloadOutlined />}
                    onClick={loadHistory}
                    loading={historyLoading}
                  >
                    刷新
                  </Button>
                }
              >
                <Table
                  columns={historyColumns}
                  dataSource={history}
                  rowKey={(record) => `${record.timestamp}-${record.type || ''}-${record.message?.slice(0, 20) || ''}`}
                  loading={historyLoading}
                  pagination={{ pageSize: 20 }}
                  size="small"
                />
              </Card>
            )
          },
          {
            key: 'stats',
            label: (
              <span>
                <BarChartOutlined />
                告警统计
              </span>
            ),
            children: (
              <Card
                title="告警统计"
                size="small"
                extra={
                  <Button
                    icon={<ReloadOutlined />}
                    onClick={loadStats}
                    loading={statsLoading}
                  >
                    刷新
                  </Button>
                }
              >
                {stats ? (
                  <>
                    <Row gutter={16}>
                      <Col span={6}>
                        <Statistic title="总告警数" value={stats.total_alerts} />
                      </Col>
                      <Col span={18}>
                        <div className={styles.statsSection}>
                          <div className={styles.statsTitle}>启用的渠道</div>
                          <Space>
                            {stats.enabled_channels.length > 0 ? (
                              stats.enabled_channels.map(ch => (
                                <Tag key={ch} color="green">{CHANNEL_NAMES[ch] || ch}</Tag>
                              ))
                            ) : (
                              <Tag>无</Tag>
                            )}
                          </Space>
                        </div>
                      </Col>
                    </Row>
                    <Divider />
                    <Row gutter={16}>
                      <Col span={12}>
                        <div className={styles.statsSection}>
                          <div className={styles.statsTitle}>按级别统计</div>
                          <Space wrap>
                            {Object.entries(stats.by_level).map(([level, count]) => (
                              <Tag key={level} color={LEVEL_COLORS[level]}>
                                {level}: {count}
                              </Tag>
                            ))}
                          </Space>
                        </div>
                      </Col>
                      <Col span={12}>
                        <div className={styles.statsSection}>
                          <div className={styles.statsTitle}>按来源统计</div>
                          <Space wrap>
                            {Object.entries(stats.by_source).map(([source, count]) => (
                              <Tag key={source}>
                                {source}: {count}
                              </Tag>
                            ))}
                          </Space>
                        </div>
                      </Col>
                    </Row>
                  </>
                ) : (
                  <Empty description="暂无统计数据" />
                )}
              </Card>
            )
          }
        ]}
      />

      {/* Webhook 编辑弹窗 */}
      <Modal
        title={editingWebhook?.index === -1 ? '添加 Webhook' : '编辑 Webhook'}
        open={webhookModalVisible}
        onOk={handleWebhookSubmit}
        onCancel={() => {
          setWebhookModalVisible(false)
          setEditingWebhook(null)
          webhookForm.resetFields()
        }}
        forceRender
      >
        <Form form={webhookForm} layout="vertical">
          <Form.Item
            label="名称"
            name="name"
            rules={[{ required: true, message: '请输入名称' }]}
          >
            <Input placeholder="如：生产环境告警" />
          </Form.Item>
          <Form.Item
            label="Webhook URL"
            name="url"
            rules={[
              { required: true, message: '请输入 Webhook URL' },
              { type: 'url', message: '请输入有效的 URL' }
            ]}
          >
            <Input placeholder="https://..." />
          </Form.Item>
          <Form.Item
            label="告警级别"
            name="levels"
            rules={[{ required: true, message: '请选择告警级别' }]}
          >
            <Select mode="multiple" placeholder="选择告警级别">
              {ALERT_LEVELS.map(level => (
                <Option key={level} value={level}>
                  <Tag color={LEVEL_COLORS[level]}>{level}</Tag>
                </Option>
              ))}
            </Select>
          </Form.Item>
          <Form.Item
            label="启用"
            name="enabled"
            valuePropName="checked"
          >
            <Switch />
          </Form.Item>
        </Form>
      </Modal>

      {/* 邮件配置弹窗 */}
      <Modal
        title="邮件告警配置"
        open={emailModalVisible}
        onOk={handleEmailConfigSubmit}
        onCancel={() => {
          setEmailModalVisible(false)
          emailForm.resetFields()
        }}
        width={600}
        forceRender
      >
        <Form form={emailForm} layout="vertical">
          <Row gutter={16}>
            <Col span={16}>
              <Form.Item
                label="SMTP服务器"
                name="smtp_host"
                rules={[{ required: true, message: '请输入SMTP服务器地址' }]}
              >
                <Input placeholder="如：smtp.qq.com" />
              </Form.Item>
            </Col>
            <Col span={8}>
              <Form.Item
                label="端口"
                name="smtp_port"
                rules={[{ required: true, message: '请输入端口' }]}
              >
                <InputNumber min={1} max={65535} style={{ width: '100%' }} />
              </Form.Item>
            </Col>
          </Row>
          <Row gutter={16}>
            <Col span={12}>
              <Form.Item
                label="用户名"
                name="smtp_user"
                rules={[{ required: true, message: '请输入用户名' }]}
              >
                <Input placeholder="发件邮箱地址" />
              </Form.Item>
            </Col>
            <Col span={12}>
              <Form.Item
                label="密码/授权码"
                name="smtp_password"
                rules={[{ required: true, message: '请输入密码' }]}
              >
                <Input.Password placeholder="SMTP密码或授权码" />
              </Form.Item>
            </Col>
          </Row>
          <Row gutter={16}>
            <Col span={12}>
              <Form.Item
                label="发件人名称"
                name="sender_name"
              >
                <Input placeholder="AntCode告警系统" />
              </Form.Item>
            </Col>
            <Col span={12}>
              <Form.Item
                label="使用SSL"
                name="smtp_ssl"
                valuePropName="checked"
              >
                <Switch />
              </Form.Item>
            </Col>
          </Row>
        </Form>
      </Modal>

      {/* 收件人编辑弹窗 */}
      <Modal
        title={editingRecipient?.index === -1 ? '添加收件人' : '编辑收件人'}
        open={recipientModalVisible}
        onOk={handleRecipientSubmit}
        onCancel={() => {
          setRecipientModalVisible(false)
          setEditingRecipient(null)
          recipientForm.resetFields()
        }}
        forceRender
      >
        <Form form={recipientForm} layout="vertical">
          <Form.Item
            label="邮箱地址"
            name="email"
            rules={[
              { required: true, message: '请输入邮箱地址' },
              { type: 'email', message: '请输入有效的邮箱地址' }
            ]}
          >
            <Input placeholder="收件人邮箱" />
          </Form.Item>
          <Form.Item
            label="名称"
            name="name"
          >
            <Input placeholder="收件人名称（可选）" />
          </Form.Item>
          <Form.Item
            label="告警级别"
            name="levels"
            rules={[{ required: true, message: '请选择告警级别' }]}
          >
            <Select mode="multiple" placeholder="选择告警级别">
              {ALERT_LEVELS.map(level => (
                <Option key={level} value={level}>
                  <Tag color={LEVEL_COLORS[level]}>{level}</Tag>
                </Option>
              ))}
            </Select>
          </Form.Item>
        </Form>
      </Modal>
    </div>
  )
})

export default AlertConfig
