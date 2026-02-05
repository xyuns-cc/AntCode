import React, { useState, useEffect, useCallback, useMemo } from 'react'
import { useParams, useNavigate } from 'react-router-dom'
import {
  Card,
  Button,
  Space,
  Empty,
  Tag,
  Row,
  Col,
  Spin,
  Alert,
  Tooltip,
  Progress,
  Typography,
  theme
} from 'antd'
import showNotification from '@/utils/notification'
import { 
  ArrowLeftOutlined, 
  ReloadOutlined, 
  InfoCircleOutlined,
  CheckCircleOutlined,
  CloseCircleOutlined,
  ClockCircleOutlined,
  ThunderboltOutlined
} from '@ant-design/icons'
import EnhancedLogViewer from '@/components/ui/LogViewer/EnhancedLogViewer'
import CopyableTooltip from '@/components/common/CopyableTooltip'
import { taskService } from '@/services/tasks'
import type { Task, TaskExecution } from '@/types'
import { formatDateTime, formatDuration } from '@/utils/format'
import Logger from '@/utils/logger'

const { Title, Text } = Typography

const ExecutionLogs: React.FC = () => {
  const navigate = useNavigate()
  const { taskId, executionId } = useParams<{ taskId: string; executionId: string }>()
  const { token } = theme.useToken()
  
  // 状态管理
  const [task, setTask] = useState<Task | null>(null)
  const [execution, setExecution] = useState<TaskExecution | null>(null)
  const [loading, setLoading] = useState(true)
  const [refreshing, setRefreshing] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [viewerHeight, setViewerHeight] = useState(600)

  // 加载任务信息
  const loadTask = useCallback(async () => {
    if (!taskId) return
    
    try {
      const taskData = await taskService.getTask(taskId)
      setTask(taskData)
    } catch (error: unknown) {
      console.error('加载任务信息失败:', error)
      // 不显示错误，因为主要关注执行日志
    }
  }, [taskId])

  // 加载执行信息
  const loadExecution = useCallback(async () => {
    if (!taskId || !executionId) {
      setError('缺少任务ID或执行ID')
      setLoading(false)
      return
    }
    
    try {
      const executions = await taskService.getTaskExecutions(taskId)
      const exec = executions.items.find(e => e.execution_id === executionId)
      
      if (exec) {
        setExecution(exec)
        setError(null)
      } else {
        setError(`未找到执行记录: ${executionId}`)
      }
    } catch (error: unknown) {
      console.error('加载执行信息失败:', error)
      const errMsg = error instanceof Error ? error.message : '加载执行信息失败'
      setError(errMsg)
    } finally {
      setLoading(false)
    }
  }, [taskId, executionId])

  // 刷新执行状态
  const refreshExecution = useCallback(async () => {
    setRefreshing(true)
    try {
      await loadExecution()
      showNotification('success', '执行状态已刷新')
    } catch (_error) {
      showNotification('error', '刷新失败')
    } finally {
      setRefreshing(false)
    }
  }, [loadExecution])

  // 初始加载
  useEffect(() => {
    const init = async () => {
      setLoading(true)
      await Promise.all([loadTask(), loadExecution()])
    }
    init()
  }, [loadTask, loadExecution])

  // 自动刷新（如果任务正在运行）
  useEffect(() => {
    if (!execution || execution.status === 'success' || execution.status === 'failed') {
      return
    }

    const interval = setInterval(() => {
      loadExecution()
    }, 10000) // 每10秒刷新一次

    return () => clearInterval(interval)
  }, [execution, loadExecution])

  // 根据视口高度动态调整日志查看器高度
  useEffect(() => {
    const updateHeight = () => {
      if (typeof window === 'undefined') return
      const viewportHeight = window.innerHeight || 800
      const reserved = execution ? 420 : 320
      setViewerHeight(Math.max(viewportHeight - reserved, 360))
    }

    updateHeight()
    window.addEventListener('resize', updateHeight)

    return () => {
      window.removeEventListener('resize', updateHeight)
    }
  }, [execution])

  // 计算执行进度（如果有持续时间）
  const executionProgress = useMemo(() => {
    if (!execution) return null
    
    if (execution.status === 'success') return 100
    if (execution.status === 'failed') return 100
    if (!execution.start_time) return 0
    
    const startTime = new Date(execution.start_time).getTime()
    const now = Date.now()
    const elapsed = (now - startTime) / 1000 // 秒
    
    // 假设一般任务在5分钟内完成
    const estimatedDuration = 300
    const progress = Math.min((elapsed / estimatedDuration) * 100, 99)
    
    return Math.round(progress)
  }, [execution])

  const statusMeta = useMemo(() => {
    const base = {
      tagColor: 'default' as 'default' | 'success' | 'error' | 'processing' | 'warning',
      text: execution ? execution.status?.toUpperCase() || '未知状态' : '等待执行',
      icon: <InfoCircleOutlined style={{ color: token.colorInfo }} />
    }

    if (!execution) {
      return base
    }

    switch (execution.status) {
      case 'success':
        return {
          tagColor: 'success' as const,
          text: '执行成功',
          icon: <CheckCircleOutlined style={{ color: token.colorSuccess }} />
        }
      case 'failed':
        return {
          tagColor: 'error' as const,
          text: '执行失败',
          icon: <CloseCircleOutlined style={{ color: token.colorError }} />
        }
      case 'running':
        return {
          tagColor: 'processing' as const,
          text: '运行中',
          icon: <ClockCircleOutlined style={{ color: token.colorWarning }} spin />
        }
      default:
        return {
          ...base,
          text: execution.status?.toUpperCase() || '未知状态'
        }
    }
  }, [execution, token.colorError, token.colorInfo, token.colorSuccess, token.colorWarning])

  const durationDisplay = useMemo(() => {
    if (!execution) return '-'
    if (execution.duration_seconds) return formatDuration(execution.duration_seconds)
    if (execution.start_time) {
      const seconds = Math.max(
        0,
        Math.floor((Date.now() - new Date(execution.start_time).getTime()) / 1000)
      )
      return `${formatDuration(seconds)}（进行中）`
    }
    return '-'
  }, [execution])

  const endTimeDisplay = useMemo(() => {
    if (!execution) return '-'
    if (!execution.end_time) {
      return (
        <Tag 
          color="processing"
          style={{ display: 'inline-flex', alignItems: 'center', gap: '4px' }}
        >
          <ClockCircleOutlined spin style={{ fontSize: 12 }} />
          <span>运行中</span>
        </Tag>
      )
    }
    return formatDateTime(execution.end_time)
  }, [execution])

  const overviewItems = useMemo(
    () =>
      execution
        ? [
            {
              key: 'executionId',
              label: '执行ID',
              value: (
                <CopyableTooltip text={execution.execution_id}>
                  <code style={{ cursor: 'pointer' }}>
                    {execution.execution_id}
                  </code>
                </CopyableTooltip>
              )
            },
            {
              key: 'startTime',
              label: '开始时间',
              value: formatDateTime(execution.start_time)
            },
            {
              key: 'endTime',
              label: '结束时间',
              value: endTimeDisplay
            },
            {
              key: 'duration',
              label: '持续时间',
              value: durationDisplay
            },
            {
              key: 'exitCode',
              label: '退出码',
              value:
                execution.exit_code !== null && execution.exit_code !== undefined ? (
                  <Tag color={execution.exit_code === 0 ? 'success' : 'error'}>
                    {execution.exit_code}
                  </Tag>
                ) : (
                  '-'
                )
            },
            {
              key: 'taskId',
              label: '任务ID',
              value: taskId ? `#${taskId}` : '-'
            }
          ]
        : [],
    [durationDisplay, endTimeDisplay, execution, taskId]
  )

  // 如果正在加载
  if (loading) {
    return (
      <div style={{ 
        padding: '24px', 
        display: 'flex', 
        justifyContent: 'center', 
        alignItems: 'center',
        minHeight: '400px'
      }}>
        <Spin size="large">
          <div style={{ marginTop: '50px' }}>加载执行日志中...</div>
        </Spin>
      </div>
    )
  }

  // 如果有错误
  if (error && !execution) {
    return (
      <div style={{ padding: '24px' }}>
        <Card>
          <div style={{ textAlign: 'center', padding: '40px' }}>
            <CloseCircleOutlined style={{ fontSize: 48, color: token.colorError, marginBottom: 16 }} />
            <h3>加载失败</h3>
            <p style={{ color: token.colorTextSecondary, marginBottom: 24 }}>{error}</p>
            <Space>
              <Button 
                icon={<ArrowLeftOutlined />}
                onClick={() => navigate(`/tasks/${taskId}`)}
              >
                返回任务详情
              </Button>
              <Button 
                type="primary" 
                icon={<ReloadOutlined />}
                onClick={() => {
                  setError(null)
                  setLoading(true)
                  loadExecution()
                }}
              >
                重试
              </Button>
            </Space>
          </div>
        </Card>
      </div>
    )
  }

  return (
    <div style={{ 
      padding: '20px 24px', 
      maxWidth: '1800px', 
      margin: '0 auto',
      minHeight: '100vh'
    }}>
      {/* 页面头部 - 更紧凑的设计 */}
      <div
        style={{
          marginBottom: '20px',
          padding: '16px 24px',
          background: token.colorBgContainer,
          borderRadius: '8px',
          boxShadow: '0 1px 2px 0 rgba(0, 0, 0, 0.03), 0 1px 6px -1px rgba(0, 0, 0, 0.02)',
          border: `1px solid ${token.colorBorderSecondary}`
        }}
      >
        <Row justify="space-between" align="middle" gutter={[16, 12]}>
          <Col xs={24} lg={18}>
            <Space size="middle" wrap>
              <Button
                icon={<ArrowLeftOutlined />}
                onClick={() => navigate(`/tasks/${taskId}`)}
                size="large"
              >
                返回
              </Button>
              <div style={{ 
                borderLeft: `2px solid ${token.colorBorderSecondary}`,
                paddingLeft: 16,
                height: 36,
                display: 'flex',
                flexDirection: 'column',
                justifyContent: 'center'
              }}>
                <Title level={5} style={{ margin: 0, lineHeight: 1.2 }}>
                  执行日志
                </Title>
                <Text type="secondary" style={{ fontSize: 12 }}>
                  {task ? task.name : '加载中...'}
                </Text>
              </div>
              <Tag
                color={statusMeta.tagColor}
                icon={statusMeta.icon}
                style={{ 
                  margin: 0, 
                  padding: '4px 12px',
                  fontSize: 13,
                  display: 'flex', 
                  alignItems: 'center', 
                  gap: 6,
                  border: 'none'
                }}
              >
                {statusMeta.text}
              </Tag>
            </Space>
          </Col>
          <Col xs={24} lg={6} style={{ textAlign: 'right' }}>
            <Button
              icon={<ReloadOutlined />}
              onClick={refreshExecution}
              loading={refreshing}
              type="default"
            >
              刷新状态
            </Button>
          </Col>
        </Row>
      </div>

      {/* 错误提示 */}
      {error && (
        <Alert
          message="加载警告"
          description={error}
          type="warning"
          showIcon
          closable
          style={{ marginBottom: '20px' }}
          onClose={() => setError(null)}
        />
      )}

      {/* 执行概览 - 优化为更现代的卡片式布局 */}
      {execution && (
        <div
          style={{
            marginBottom: '20px',
            padding: '24px',
            background: token.colorBgContainer,
            borderRadius: '8px',
            boxShadow: '0 1px 2px 0 rgba(0, 0, 0, 0.03), 0 1px 6px -1px rgba(0, 0, 0, 0.02)',
            border: `1px solid ${token.colorBorderSecondary}`
          }}
        >
          {/* 标题区域 */}
          <div style={{ 
            display: 'flex', 
            justifyContent: 'space-between', 
            alignItems: 'center',
            marginBottom: 20,
            paddingBottom: 16,
            borderBottom: `1px solid ${token.colorBorderSecondary}`
          }}>
            <Space>
              <ThunderboltOutlined style={{ fontSize: 18, color: token.colorPrimary }} />
              <Text strong style={{ fontSize: 16 }}>执行详情</Text>
            </Space>
            {execution.status === 'running' && executionProgress !== null && (
              <Tooltip title={`预估进度 ${executionProgress}%`} placement="topLeft">
                <Progress
                  type="circle"
                  percent={executionProgress}
                  width={44}
                  format={(percent) => (
                    <span style={{ fontSize: 12, fontWeight: 600 }}>
                      {percent}%
                    </span>
                  )}
                />
              </Tooltip>
            )}
          </div>

          {/* 信息网格 */}
          <Row gutter={[20, 20]}>
            {overviewItems.map(item => {
              const valueContent =
                typeof item.value === 'string' || typeof item.value === 'number'
                  ? <Text strong style={{ fontSize: 15 }}>{item.value}</Text>
                  : item.value

              return (
                <Col key={item.key} xs={24} sm={12} lg={8}>
                  <div
                    style={{
                      padding: '16px 18px',
                      borderRadius: 6,
                      background: token.colorFillQuaternary,
                      border: `1px solid ${token.colorBorder}`,
                      transition: 'all 0.2s',
                      cursor: 'default',
                      height: '100%'
                    }}
                    onMouseEnter={(e) => {
                      e.currentTarget.style.background = token.colorFillTertiary
                      e.currentTarget.style.borderColor = token.colorPrimaryBorder
                    }}
                    onMouseLeave={(e) => {
                      e.currentTarget.style.background = token.colorFillQuaternary
                      e.currentTarget.style.borderColor = token.colorBorder
                    }}
                  >
                    <div style={{ 
                      fontSize: 12, 
                      color: token.colorTextSecondary,
                      marginBottom: 8,
                      fontWeight: 500
                    }}>
                      {item.label}
                    </div>
                    <div style={{ 
                      fontSize: 15, 
                      fontWeight: 600, 
                      color: token.colorText,
                      lineHeight: 1.4
                    }}>
                      {valueContent}
                    </div>
                  </div>
                </Col>
              )
            })}
          </Row>
        </div>
      )}

      {/* 日志查看器 */}
      {executionId ? (
        <div style={{
          background: token.colorBgContainer,
          borderRadius: '8px',
          overflow: 'hidden',
          boxShadow: '0 1px 2px 0 rgba(0, 0, 0, 0.03), 0 1px 6px -1px rgba(0, 0, 0, 0.02)',
          border: `1px solid ${token.colorBorderSecondary}`
        }}>
          <EnhancedLogViewer
            key={executionId}
            executionId={executionId}
            height={viewerHeight}
            showControls={true}
            autoConnect={true}
            showStdout={true}
            showStderr={true}
            maxLines={5000}
            enableSearch={true}
            enableExport={true}
            enableVirtualization={true}
            onStatusUpdate={(statusUpdate) => {
              // 收到状态更新时，更新本地执行状态
              Logger.info('收到执行状态更新', statusUpdate)
              if (execution && statusUpdate.status) {
                setExecution(prev => prev ? {
                  ...prev,
                  status: statusUpdate.status as TaskExecution['status']
                } : null)
              }
              // 如果任务完成，刷新完整的执行信息
              if (['success', 'failed', 'timeout', 'cancelled'].includes(statusUpdate.status)) {
                loadExecution()
              }
            }}
          />
        </div>
      ) : (
        <Card>
          <div style={{
            padding: '80px 40px',
            textAlign: 'center'
          }}>
            <Empty 
              description="执行ID不存在"
              image={Empty.PRESENTED_IMAGE_SIMPLE}
            />
          </div>
        </Card>
      )}
    </div>
  )
}

export default ExecutionLogs
