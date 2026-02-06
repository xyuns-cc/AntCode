/**
 * Worker 资源管理组件
 * 管理员可查看，超级管理员可修改
 */
import type React from 'react'
import { useState, useEffect, useCallback } from 'react'
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
  Flex,
  Typography,
  theme
} from 'antd'
import {
  ThunderboltOutlined,
  DatabaseOutlined,
  ClockCircleOutlined,
  SyncOutlined,
  SaveOutlined,
  ReloadOutlined,
  InfoCircleOutlined,
  DesktopOutlined,
  HddOutlined
} from '@ant-design/icons'
import { workerService } from '@/services/workers'
import { useAuthStore } from '@/stores/authStore'
import showNotification from '@/utils/notification'

const { Text } = Typography

interface WorkerResourceManagementProps {
  workerId: string
  workerName?: string
}

interface ResourceData {
  limits: {
    max_concurrent_tasks: number
    task_memory_limit_mb: number
    task_cpu_time_limit_sec: number
    task_timeout?: number
  }
  auto_adjustment: boolean
  resource_stats: {
    cpu_percent: number
    memory_percent: number
    disk_percent: number
    memory_used_mb: number
    memory_total_mb: number
    disk_used_gb: number
    disk_total_gb: number
    running_tasks: number
    queued_tasks: number
    uptime_seconds: number
  }
}

const getErrorMessage = (err: unknown, fallback: string): string =>
  err instanceof Error ? err.message : fallback

const WorkerResourceManagement: React.FC<WorkerResourceManagementProps> = ({ workerId }) => {
  const { token } = theme.useToken()
  const { user } = useAuthStore()
  const [form] = Form.useForm()

  const [loading, setLoading] = useState(true)
  const [saving, setSaving] = useState(false)
  const [resourceData, setResourceData] = useState<ResourceData | null>(null)
  const [error, setError] = useState<string | null>(null)

  const isSuperAdmin = user?.username === 'admin'
  const isAdmin = user?.is_admin

  const loadResources = useCallback(async () => {
    setLoading(true)
    setError(null)
    try {
      const data = await workerService.getWorkerResources(workerId)
      setResourceData(data)
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
  }, [workerId, form])

  useEffect(() => {
    if (isAdmin) {
      loadResources()
    }
  }, [isAdmin, loadResources])

  const handleSave = async () => {
    if (!isSuperAdmin) {
      showNotification('error', '需要超级管理员权限')
      return
    }
    try {
      const values = await form.validateFields()
      setSaving(true)
      await workerService.updateWorkerResources(workerId, {
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
        action={<Button size="small" onClick={loadResources}>重试</Button>}
      />
    )
  }

  if (!resourceData) return null

  const { limits, resource_stats } = resourceData
  const cpuPercent = resource_stats?.cpu_percent ?? 0
  const memoryPercent = resource_stats?.memory_percent ?? 0
  const memoryTotalMb = resource_stats?.memory_total_mb ?? 0
  const memoryUsedMb = resource_stats?.memory_used_mb ?? 0
  const memoryAvailableMb = Math.max(0, memoryTotalMb - memoryUsedMb)
  const memoryAvailable = memoryAvailableMb / 1024
  const memoryTotal = memoryTotalMb / 1024

  return (
    <div style={{ padding: '8px 0' }}>
      {/* 实时资源监控 */}
      <Card
        size="small"
        title="实时资源状态"
        extra={
          <Button size="small" icon={<ReloadOutlined />} onClick={loadResources}>
            刷新
          </Button>
        }
        style={{ marginBottom: 16 }}
      >
        <Row gutter={24}>
          {/* CPU 使用率 */}
          <Col span={8}>
            <Flex vertical align="center" gap={8}>
              <Text type="secondary">CPU 使用率</Text>
              <Progress
                type="circle"
                percent={Math.round(cpuPercent)}
                size={72}
                strokeColor={cpuPercent > 80 ? token.colorError : cpuPercent > 60 ? token.colorWarning : token.colorSuccess}
                format={(percent) => `${percent}%`}
              />
            </Flex>
          </Col>
          {/* 内存使用率 */}
          <Col span={8}>
            <Flex vertical align="center" gap={8}>
              <Text type="secondary">内存使用率</Text>
              <Progress
                type="circle"
                percent={Math.round(memoryPercent)}
                size={72}
                strokeColor={memoryPercent > 80 ? token.colorError : memoryPercent > 60 ? token.colorWarning : token.colorSuccess}
                format={(percent) => `${percent}%`}
              />
            </Flex>
          </Col>
          {/* 内存详情 */}
          <Col span={8}>
            <Flex vertical gap={12} style={{ height: '100%', justifyContent: 'center' }}>
              <Flex align="center" gap={8}>
                <HddOutlined style={{ color: token.colorSuccess }} />
                <Text type="secondary">可用:</Text>
                <Text strong>{memoryAvailable.toFixed(1)} GB</Text>
              </Flex>
              <Flex align="center" gap={8}>
                <DesktopOutlined style={{ color: token.colorPrimary }} />
                <Text type="secondary">总计:</Text>
                <Text strong>{memoryTotal.toFixed(1)} GB</Text>
              </Flex>
            </Flex>
          </Col>
        </Row>
      </Card>

      {/* 资源限制配置 */}
      <Card
        size="small"
        title={
          <Space>
            资源限制配置
            {!isSuperAdmin && (
              <Tooltip title="需要超级管理员权限才能修改">
                <InfoCircleOutlined style={{ color: token.colorTextSecondary }} />
              </Tooltip>
            )}
          </Space>
        }
      >
        <Form form={form} layout="vertical" disabled={!isSuperAdmin}>
          <Row gutter={16}>
            <Col span={12}>
              <Form.Item
                name="max_concurrent_tasks"
                label={
                  <Space size={4}>
                    <ThunderboltOutlined />
                    最大并发任务数
                  </Space>
                }
                rules={[{ required: true }, { type: 'number', min: 1, max: 20 }]}
              >
                <InputNumber min={1} max={20} style={{ width: '100%' }} addonAfter="个" />
              </Form.Item>
            </Col>
            <Col span={12}>
              <Form.Item
                name="task_memory_limit_mb"
                label={
                  <Space size={4}>
                    <DatabaseOutlined />
                    单任务内存限制
                  </Space>
                }
                rules={[{ required: true }, { type: 'number', min: 256, max: 8192 }]}
              >
                <InputNumber min={256} max={8192} step={256} style={{ width: '100%' }} addonAfter="MB" />
              </Form.Item>
            </Col>
          </Row>

          <Row gutter={16}>
            <Col span={12}>
              <Form.Item
                name="task_cpu_time_limit_sec"
                label={
                  <Space size={4}>
                    <ClockCircleOutlined />
                    单任务 CPU 时间限制
                  </Space>
                }
                rules={[{ required: true }, { type: 'number', min: 60, max: 3600 }]}
              >
                <InputNumber min={60} max={3600} step={60} style={{ width: '100%' }} addonAfter="秒" />
              </Form.Item>
            </Col>
            <Col span={12}>
              <Form.Item
                name="auto_resource_limit"
                label={
                  <Space size={4}>
                    <SyncOutlined />
                    自适应资源限制
                  </Space>
                }
                valuePropName="checked"
              >
                <Switch checkedChildren="启用" unCheckedChildren="禁用" />
              </Form.Item>
            </Col>
          </Row>

          {/* 当前生效配置 */}
          <Card size="small" style={{ background: token.colorFillQuaternary, marginBottom: 16 }}>
            <Row gutter={16}>
              <Col span={6}>
                <Statistic title="最大并发" value={limits.max_concurrent_tasks} suffix="个" valueStyle={{ fontSize: 16 }} />
              </Col>
              <Col span={6}>
                <Statistic title="内存限制" value={limits.task_memory_limit_mb} suffix="MB" valueStyle={{ fontSize: 16 }} />
              </Col>
              <Col span={6}>
                <Statistic title="CPU 时限" value={limits.task_cpu_time_limit_sec} suffix="秒" valueStyle={{ fontSize: 16 }} />
              </Col>
              <Col span={6}>
                <Statistic title="任务超时" value={limits.task_timeout} suffix="秒" valueStyle={{ fontSize: 16 }} />
              </Col>
            </Row>
          </Card>

          {isSuperAdmin ? (
            <Flex justify="flex-end" gap={8}>
              <Button onClick={loadResources}>重置</Button>
              <Button type="primary" icon={<SaveOutlined />} onClick={handleSave} loading={saving}>
                保存配置
              </Button>
            </Flex>
          ) : (
            <Alert
              message="只读模式"
              description="需要超级管理员（admin）权限才能修改配置"
              type="info"
              showIcon
            />
          )}
        </Form>
      </Card>
    </div>
  )
}

export default WorkerResourceManagement
