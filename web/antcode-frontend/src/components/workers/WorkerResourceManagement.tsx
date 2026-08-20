/**
 * Worker 资源管理组件
 * 管理员可查看，超级管理员可修改
 */
import type React from 'react'
import { useState, useEffect, useCallback } from 'react'
import { Card, Form, Button, Space, Row, Col, Progress, Alert, Tooltip, Spin, Flex, Typography, theme } from 'antd'
import {
  SaveOutlined,
  ReloadOutlined,
  InfoCircleOutlined,
  DesktopOutlined,
  HddOutlined
} from '@ant-design/icons'
import { workerService } from '@/services/workers'
import { useAuthStore } from '@/stores/authStore'
import showNotification from '@/utils/notification'
import type { WorkerResourceInfo } from '@/types'
import WorkerResourceLimitsCard from './WorkerResourceLimitsCard'
import WorkerResourceLimitsFields from './WorkerResourceLimitsFields'

const { Text } = Typography

interface WorkerResourceManagementProps {
  workerId: string
  workerName?: string
}

// 组件不再自带一份结构副本：那份副本是 limits.task_timeout（后端从不返回）的来源。
type ResourceData = WorkerResourceInfo

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

  const isSuperAdmin = user?.role === 'super_admin'
  const isAdmin = user?.is_admin

  const loadResources = useCallback(async () => {
    setLoading(true)
    setError(null)
    try {
      const data = await workerService.getWorkerResources(workerId)
      setResourceData(data)
      // 表单是"要下发什么"，先取控制面已下发值；没下发过就退到执行面生效值，
      // 两边都没有就留空，让人显式填——不拿任何一个默认值把空当成现状。
      form.setFieldsValue({
        max_concurrent_tasks: data.configured_limits.max_concurrent_tasks ?? data.limits.max_concurrent_tasks ?? undefined,
        task_memory_limit_mb: data.configured_limits.task_memory_limit_mb ?? data.limits.task_memory_limit_mb ?? undefined,
        task_cpu_time_limit_sec:
          data.configured_limits.task_cpu_time_limit_sec ?? data.limits.task_cpu_time_limit_sec ?? undefined,
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
      const result = await workerService.updateWorkerResources(workerId, {
        max_concurrent_tasks: values.max_concurrent_tasks,
        task_memory_limit_mb: values.task_memory_limit_mb,
        task_cpu_time_limit_sec: values.task_cpu_time_limit_sec,
        auto_resource_limit: values.auto_resource_limit
      })
      // 不说"已更新"：这里下发的是请求。Worker 按自身内存预算校验，超卖时会重算
      // 收敛或直接拒绝，生效值以它上报的为准（对照下方"生效/已配置"两行）。
      // synced=false 表示控制事件根本没写进去，那连"下发"都没发生，不能报成功。
      if (result.synced) {
        showNotification('success', '资源配置已下发，生效值以 Worker 上报为准')
      } else {
        showNotification('warning', '配置已入库，但未能下发到 Worker，当前生效值不变')
      }
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

  const { limits, configured_limits, resource_stats } = resourceData
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
          <WorkerResourceLimitsFields />

          <WorkerResourceLimitsCard limits={limits} configuredLimits={configured_limits} />

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
