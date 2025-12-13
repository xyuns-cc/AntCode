/**
 * 节点资源管理组件
 * 管理员可查看，超级管理员可修改
 */
import React, { useState, useEffect, useCallback } from 'react'
import {
  Card,
  Form,
  InputNumber,
  Switch,
  Button,
  Space,
  Statistic,
  Row,
  Col,
  Progress,
  Alert,
  Tooltip,
  Spin,
  Descriptions,
  Tag,
  Divider,
  theme
} from 'antd'
import {
  ThunderboltOutlined,
  DatabaseOutlined,
  ClockCircleOutlined,
  SyncOutlined,
  SaveOutlined,
  ReloadOutlined,
  InfoCircleOutlined
} from '@ant-design/icons'
import { nodeService } from '@/services/nodes'
import { useAuthStore } from '@/stores/authStore'
import showNotification from '@/utils/notification'

interface NodeResourceManagementProps {
  nodeId: string
  nodeName?: string  // 可选，用于显示
}

interface ResourceData {
  limits: {
    max_concurrent_tasks: number
    task_memory_limit_mb: number
    task_cpu_time_limit_sec: number
    task_timeout: number
  }
  auto_adjustment: boolean
  resource_stats: {
    cpu_percent: number
    memory_percent: number
    memory_available_gb: number
    memory_total_gb: number
    cpu_history_avg: number
    memory_history_avg: number
    current_limits: {
      max_concurrent_tasks: number
      task_memory_limit_mb: number
      task_cpu_time_limit_sec: number
    }
    auto_adjustment_enabled: boolean
    monitoring_active: boolean
  }
}

const getErrorMessage = (err: unknown, fallback: string): string =>
  err instanceof Error ? err.message : fallback

const NodeResourceManagement: React.FC<NodeResourceManagementProps> = ({ nodeId }) => {
  const { token } = theme.useToken()
  const { user } = useAuthStore()
  const [form] = Form.useForm()
  
  const [loading, setLoading] = useState(true)
  const [saving, setSaving] = useState(false)
  const [resourceData, setResourceData] = useState<ResourceData | null>(null)
  const [error, setError] = useState<string | null>(null)

  // 是否为超级管理员（可修改）
  const isSuperAdmin = user?.username === 'admin'
  // 是否为管理员（可查看）
  const isAdmin = user?.is_admin

  // 加载资源数据
  const loadResources = useCallback(async () => {
    setLoading(true)
    setError(null)
    try {
      const data = await nodeService.getNodeResources(nodeId)
      setResourceData(data)
      // 设置表单初始值
      form.setFieldsValue({
        max_concurrent_tasks: data.limits.max_concurrent_tasks,
        task_memory_limit_mb: data.limits.task_memory_limit_mb,
        task_cpu_time_limit_sec: data.limits.task_cpu_time_limit_sec,
        auto_resource_limit: data.auto_adjustment
      })
    } catch (err: unknown) {
      setError(getErrorMessage(err, '获取资源信息失败'))
    } finally {
      setLoading(false)
    }
  }, [nodeId, form])

  useEffect(() => {
    if (isAdmin) {
      loadResources()
    }
  }, [isAdmin, loadResources])

  // 保存配置
  const handleSave = async () => {
    if (!isSuperAdmin) {
      showNotification('error', '需要超级管理员权限')
      return
    }

    try {
      const values = await form.validateFields()
      setSaving(true)
      
      await nodeService.updateNodeResources(nodeId, {
        max_concurrent_tasks: values.max_concurrent_tasks,
        task_memory_limit_mb: values.task_memory_limit_mb,
        task_cpu_time_limit_sec: values.task_cpu_time_limit_sec,
        auto_resource_limit: values.auto_resource_limit
      })
      
      showNotification('success', '资源配置已更新')
      loadResources()
    } catch (err: unknown) {
      showNotification('error', getErrorMessage(err, '保存失败'))
    } finally {
      setSaving(false)
    }
  }

  // 非管理员无法访问
  if (!isAdmin) {
    return (
      <Alert
        message="权限不足"
        description="需要管理员权限才能查看资源配置"
        type="warning"
        showIcon
      />
    )
  }

  if (loading) {
    return (
      <div style={{ textAlign: 'center', padding: 40 }}>
        <Spin size="large" />
        <div style={{ marginTop: 16, color: token.colorTextSecondary }}>
          加载资源信息...
        </div>
      </div>
    )
  }

  if (error) {
    return (
      <Alert
        message="加载失败"
        description={error}
        type="error"
        showIcon
        action={
          <Button size="small" onClick={loadResources}>
            重试
          </Button>
        }
      />
    )
  }

  if (!resourceData) return null

  const { limits, resource_stats } = resourceData

  return (
    <div>
      {/* 实时资源状态 */}
      <Card 
        size="small" 
        title={
          <Space>
            <ThunderboltOutlined />
            实时资源状态
            {resource_stats.monitoring_active && (
              <Tag color="green">监控中</Tag>
            )}
          </Space>
        }
        extra={
          <Button 
            size="small" 
            icon={<ReloadOutlined />} 
            onClick={loadResources}
          >
            刷新
          </Button>
        }
        style={{ marginBottom: 16 }}
      >
        <Row gutter={[16, 16]}>
          <Col xs={12} sm={6}>
            <Statistic
              title="CPU 使用率"
              value={resource_stats.cpu_percent}
              suffix="%"
              valueStyle={{ 
                color: resource_stats.cpu_percent > 80 ? token.colorError : token.colorSuccess 
              }}
            />
            <Progress 
              percent={resource_stats.cpu_percent} 
              size="small" 
              status={resource_stats.cpu_percent > 80 ? 'exception' : 'normal'}
              showInfo={false}
            />
          </Col>
          <Col xs={12} sm={6}>
            <Statistic
              title="内存使用率"
              value={resource_stats.memory_percent}
              suffix="%"
              valueStyle={{ 
                color: resource_stats.memory_percent > 80 ? token.colorError : token.colorSuccess 
              }}
            />
            <Progress 
              percent={resource_stats.memory_percent} 
              size="small" 
              status={resource_stats.memory_percent > 80 ? 'exception' : 'normal'}
              showInfo={false}
            />
          </Col>
          <Col xs={12} sm={6}>
            <Statistic
              title="可用内存"
              value={resource_stats.memory_available_gb}
              suffix="GB"
              precision={1}
            />
          </Col>
          <Col xs={12} sm={6}>
            <Statistic
              title="总内存"
              value={resource_stats.memory_total_gb}
              suffix="GB"
              precision={1}
            />
          </Col>
        </Row>

        {resource_stats.auto_adjustment_enabled && (
          <Alert
            message="自适应调整已启用"
            description={`CPU 历史平均: ${resource_stats.cpu_history_avg}%, 内存历史平均: ${resource_stats.memory_history_avg}%`}
            type="info"
            showIcon
            style={{ marginTop: 16 }}
          />
        )}
      </Card>

      {/* 当前限制配置 */}
      <Card 
        size="small" 
        title={
          <Space>
            <DatabaseOutlined />
            资源限制配置
            {!isSuperAdmin && (
              <Tooltip title="需要超级管理员权限才能修改">
                <InfoCircleOutlined style={{ color: token.colorTextSecondary }} />
              </Tooltip>
            )}
          </Space>
        }
      >
        <Form
          form={form}
          layout="vertical"
          disabled={!isSuperAdmin}
        >
          <Row gutter={16}>
            <Col xs={24} sm={12}>
              <Form.Item
                name="max_concurrent_tasks"
                label={
                  <Space>
                    <ThunderboltOutlined />
                    最大并发任务数
                    <Tooltip title="同时执行的最大任务数量，范围 1-20">
                      <InfoCircleOutlined style={{ color: token.colorTextSecondary }} />
                    </Tooltip>
                  </Space>
                }
                rules={[
                  { required: true, message: '请输入最大并发数' },
                  { type: 'number', min: 1, max: 20, message: '范围 1-20' }
                ]}
              >
                <InputNumber 
                  min={1} 
                  max={20} 
                  style={{ width: '100%' }}
                  addonAfter="个"
                />
              </Form.Item>
            </Col>
            <Col xs={24} sm={12}>
              <Form.Item
                name="task_memory_limit_mb"
                label={
                  <Space>
                    <DatabaseOutlined />
                    单任务内存限制
                    <Tooltip title="每个任务可使用的最大内存，范围 256-8192 MB">
                      <InfoCircleOutlined style={{ color: token.colorTextSecondary }} />
                    </Tooltip>
                  </Space>
                }
                rules={[
                  { required: true, message: '请输入内存限制' },
                  { type: 'number', min: 256, max: 8192, message: '范围 256-8192 MB' }
                ]}
              >
                <InputNumber 
                  min={256} 
                  max={8192} 
                  step={256}
                  style={{ width: '100%' }}
                  addonAfter="MB"
                />
              </Form.Item>
            </Col>
          </Row>

          <Row gutter={16}>
            <Col xs={24} sm={12}>
              <Form.Item
                name="task_cpu_time_limit_sec"
                label={
                  <Space>
                    <ClockCircleOutlined />
                    单任务 CPU 时间限制
                    <Tooltip title="每个任务的 CPU 执行时间上限，范围 60-3600 秒">
                      <InfoCircleOutlined style={{ color: token.colorTextSecondary }} />
                    </Tooltip>
                  </Space>
                }
                rules={[
                  { required: true, message: '请输入 CPU 时间限制' },
                  { type: 'number', min: 60, max: 3600, message: '范围 60-3600 秒' }
                ]}
              >
                <InputNumber 
                  min={60} 
                  max={3600} 
                  step={60}
                  style={{ width: '100%' }}
                  addonAfter="秒"
                />
              </Form.Item>
            </Col>
            <Col xs={24} sm={12}>
              <Form.Item
                name="auto_resource_limit"
                label={
                  <Space>
                    <SyncOutlined />
                    自适应资源限制
                    <Tooltip title="启用后系统会根据 CPU/内存使用率自动调整限制">
                      <InfoCircleOutlined style={{ color: token.colorTextSecondary }} />
                    </Tooltip>
                  </Space>
                }
                valuePropName="checked"
              >
                <Switch 
                  checkedChildren="启用" 
                  unCheckedChildren="禁用"
                />
              </Form.Item>
            </Col>
          </Row>

          <Divider />

          <Descriptions size="small" column={2} title="当前生效配置">
            <Descriptions.Item label="最大并发">
              {limits.max_concurrent_tasks} 个
            </Descriptions.Item>
            <Descriptions.Item label="内存限制">
              {limits.task_memory_limit_mb} MB
            </Descriptions.Item>
            <Descriptions.Item label="CPU 时间限制">
              {limits.task_cpu_time_limit_sec} 秒
            </Descriptions.Item>
            <Descriptions.Item label="任务超时">
              {limits.task_timeout} 秒
            </Descriptions.Item>
          </Descriptions>

          {isSuperAdmin && (
            <div style={{ marginTop: 16, textAlign: 'right' }}>
              <Space>
                <Button onClick={loadResources}>
                  重置
                </Button>
                <Button 
                  type="primary" 
                  icon={<SaveOutlined />}
                  onClick={handleSave}
                  loading={saving}
                >
                  保存配置
                </Button>
              </Space>
            </div>
          )}

          {!isSuperAdmin && (
            <Alert
              message="只读模式"
              description="您可以查看资源配置，但需要超级管理员（admin）权限才能修改"
              type="info"
              showIcon
              style={{ marginTop: 16 }}
            />
          )}
        </Form>
      </Card>
    </div>
  )
}

export default NodeResourceManagement
