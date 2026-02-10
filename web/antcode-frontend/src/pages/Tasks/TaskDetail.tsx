import React, { useState, useEffect, useCallback } from 'react'
import {
  Card,
  Descriptions,
  Button,
  Space,
  Tag,
  Tabs,
  Progress,
  Statistic,
  Row,
  Col,
  Empty,
  Tooltip,
  theme
} from 'antd'
import {
  ArrowLeftOutlined,
  PlayCircleOutlined,
  EditOutlined,
  DeleteOutlined,
  ReloadOutlined,
  EyeOutlined,
  CloudServerOutlined,
  RedoOutlined,
  HistoryOutlined,
  StopOutlined
} from '@ant-design/icons'
import { useNavigate, useParams } from 'react-router-dom'
import ResponsiveTable from '@/components/common/ResponsiveTable'
import CopyableTooltip from '@/components/common/CopyableTooltip'
import { taskService } from '@/services/tasks'
import { manualRetry, getRetryStats, type RetryStats } from '@/services/retry'
import type { Task, TaskExecution, TaskStatus } from '@/types'
import { formatDateTime, formatDuration, formatStatus, formatTaskType } from '@/utils/format'
import { describeCronExpression } from '@/utils/cron'
import showNotification from '@/utils/notification'

const TaskDetail: React.FC = () => {
  const navigate = useNavigate()
  const { id } = useParams<{ id: string }>()
  const { token } = theme.useToken()
  const [task, setTask] = useState<Task | null>(null)
  const [executions, setExecutions] = useState<TaskExecution[]>([])
  const [loading, setLoading] = useState(false)
  const [executionsLoading, setExecutionsLoading] = useState(false)
  const [retryStats, setRetryStats] = useState<RetryStats | null>(null)
  const [retryLoading, setRetryLoading] = useState<string | null>(null)
  const [cancelLoading, setCancelLoading] = useState<string | null>(null)

  // 加载任务详情
  const loadTask = useCallback(async () => {
    if (!id) return
    
    setLoading(true)
    try {
      const taskData = await taskService.getTask(id)
      setTask(taskData)
    } catch {
      // 错误提示由拦截器统一处理
    } finally {
      setLoading(false)
    }
  }, [id])

  // 加载执行记录
  const loadExecutions = useCallback(async () => {
    if (!id) return
    
    setExecutionsLoading(true)
    try {
      const executionData = await taskService.getTaskRuns(id)
      setExecutions(executionData.items)
    } catch {
      // 错误提示由拦截器统一处理
    } finally {
      setExecutionsLoading(false)
    }
  }, [id])

  // 加载重试统计
  const loadRetryStats = useCallback(async () => {
    if (!id) return
    try {
      const stats = await getRetryStats(id)
      setRetryStats(stats)
    } catch {
      // 忽略错误
    }
  }, [id])

  // 取消执行
  const handleCancel = async (runId: string) => {
    setCancelLoading(runId)
    try {
      const result = await taskService.cancelTaskRun(runId)
      if (result.remote_cancelled) {
        showNotification('success', '任务已取消，已发送取消指令到节点')
      } else {
        showNotification('success', '任务已取消')
      }
      loadExecutions()
      loadTask()
    } catch (error: unknown) {
      const err = error as { message?: string }
      showNotification('error', '取消失败', err.message)
    } finally {
      setCancelLoading(null)
    }
  }

  // 手动重试
  const handleRetry = async (runId: string) => {
    setRetryLoading(runId)
    try {
      await manualRetry(runId)
      showNotification('success', '任务已触发重试')
      loadExecutions()
      loadRetryStats()
    } catch (error: unknown) {
      const err = error as { message?: string }
      showNotification('error', '重试失败', err.message)
    } finally {
      setRetryLoading(null)
    }
  }

  useEffect(() => {
    loadTask()
    loadExecutions()
    loadRetryStats()
  }, [loadTask, loadExecutions, loadRetryStats])

  // 触发任务
  const handleTriggerTask = async () => {
    if (!task) return
    
    try {
      const resp = await taskService.triggerTask(task.id)
      if (resp?.message) {
        showNotification('success', resp.message)
      } else {
        showNotification('success', '任务已触发')
      }
      loadTask()
      loadExecutions()
    } catch {
      // 错误提示由全局拦截器统一处理，这里不再重复弹出
    }
  }

  // 删除任务
  const handleDeleteTask = async () => {
    if (!task) return
    
    try {
      await taskService.deleteTask(task.id)
      // 成功提示由拦截器统一处理
      navigate('/tasks')
    } catch {
      // 通知由拦截器统一处理
    }
  }

  if (!task) {
    return (
      <Card loading={loading}>
        <Empty description="任务不存在" />
      </Card>
    )
  }

  const statusConfig = formatStatus(task.status)
  const typeConfig = formatTaskType(task.task_type)

  // 执行记录表格列
  const executionColumns = [
    {
      title: '执行ID',
      dataIndex: 'run_id',
      key: 'run_id',
      width: 120,
      ellipsis: { showTitle: false },
      render: (text: string) => (
        <Tooltip title={text} placement="topLeft">
          <code style={{ fontSize: '12px' }}>{text.substring(0, 8)}...</code>
        </Tooltip>
      )
    },
    {
      title: '状态',
      dataIndex: 'status',
      key: 'status',
      width: 90,
      render: (status: TaskStatus) => {
        const config = formatStatus(status)
        return <Tag color={config.color}>{config.text}</Tag>
      }
    },
    {
      title: '开始时间',
      dataIndex: 'start_time',
      key: 'start_time',
      width: 160,
      render: (time: string) => formatDateTime(time)
    },
    {
      title: '结束时间',
      dataIndex: 'end_time',
      key: 'end_time',
      width: 160,
      render: (time: string) => time ? formatDateTime(time) : '-'
    },
    {
      title: '持续时间',
      dataIndex: 'duration_seconds',
      key: 'duration_seconds',
      width: 100,
      render: (duration: number) => duration ? formatDuration(duration) : '-'
    },
    {
      title: '退出码',
      dataIndex: 'exit_code',
      key: 'exit_code',
      width: 80,
      render: (code: number) => code !== null ? code : '-'
    },
    {
      title: '重试',
      dataIndex: 'retry_count',
      key: 'retry_count',
      width: 60
    },
    {
      title: '操作',
      key: 'actions',
      width: 180,
      fixed: 'right' as const,
      render: (_: unknown, record: TaskExecution) => (
        <Space size="small">
          <Button
            type="text"
            size="small"
            icon={<EyeOutlined />}
            onClick={() => navigate(`/tasks/${task.id}/runs/${record.run_id}`)}
          >
            日志
          </Button>
          {(record.status === 'running' || record.status === 'pending' || record.status === 'queued') && (
            <Tooltip title="取消执行">
              <Button
                type="text"
                size="small"
                danger
                icon={<StopOutlined />}
                loading={cancelLoading === record.run_id}
                onClick={() => handleCancel(record.run_id)}
              >
                取消
              </Button>
            </Tooltip>
          )}
          {(record.status === 'failed' || record.status === 'timeout') && (
            <Tooltip title="重试此执行">
              <Button
                type="text"
                size="small"
                icon={<RedoOutlined />}
                loading={retryLoading === record.run_id}
                onClick={() => handleRetry(record.run_id)}
              >
                重试
              </Button>
            </Tooltip>
          )}
        </Space>
      )
    }
  ]

  return (
    <div style={{ padding: '24px' }}>
      <Card
        title={
          <Space>
            <Button
              type="text"
              icon={<ArrowLeftOutlined />}
              onClick={() => navigate('/tasks')}
            >
              返回
            </Button>
            <span>{task.name}</span>
            <Tag color={statusConfig.color}>{statusConfig.text}</Tag>
            {!task.is_active && <Tag color="default">已禁用</Tag>}
          </Space>
        }
        extra={
          <Space>
            <Button
              type="primary"
              icon={<PlayCircleOutlined />}
              onClick={handleTriggerTask}
              disabled={!task.is_active}
            >
              立即执行
            </Button>
            <Button
              icon={<EditOutlined />}
              onClick={() => navigate(`/tasks/${task.id}/edit`)}
            >
              编辑
            </Button>
            <Button
              danger
              icon={<DeleteOutlined />}
              onClick={handleDeleteTask}
            >
              删除
            </Button>
          </Space>
        }
      >
        <Tabs 
          defaultActiveKey="overview"
          items={[
            {
              key: 'overview',
              label: '概览',
              children: (
                <Row gutter={24}>
                  <Col span={16}>
                    <Descriptions title="基本信息" bordered column={2}>
                      <Descriptions.Item label="任务名称">{task.name}</Descriptions.Item>
                      <Descriptions.Item label="任务类型">
                        <Tag color={typeConfig.color}>{typeConfig.text}</Tag>
                      </Descriptions.Item>
                      <Descriptions.Item label="项目ID">
                        <CopyableTooltip text={String(task.project_id)}>
                          <Tag color="blue" style={{ cursor: 'pointer' }}>
                            #{task.project_id}
                          </Tag>
                        </CopyableTooltip>
                      </Descriptions.Item>
                      <Descriptions.Item label="状态">
                        <Tag color={statusConfig.color}>{statusConfig.text}</Tag>
                      </Descriptions.Item>
                      <Descriptions.Item label="是否启用">
                        <Tag color={task.is_active ? 'success' : 'default'}>
                          {task.is_active ? '启用' : '禁用'}
                        </Tag>
                      </Descriptions.Item>
                      <Descriptions.Item label="调度类型">
                        {task.schedule_type === 'once' && '一次性执行'}
                        {task.schedule_type === 'interval' && '间隔执行'}
                        {task.schedule_type === 'cron' && 'Cron表达式'}
                      </Descriptions.Item>
                      <Descriptions.Item label="调度配置" span={2}>
                        {task.schedule_type === 'once' && task.scheduled_time && (
                          <span>执行时间: {formatDateTime(task.scheduled_time)}</span>
                        )}
                        {task.schedule_type === 'interval' && task.interval_seconds && (
                          <span>间隔: {task.interval_seconds}秒</span>
                        )}
                        {task.schedule_type === 'cron' && task.cron_expression && (
                          <div>
                            <div>
                              表达式: 
                              <CopyableTooltip text={task.cron_expression}>
                                <code style={{ 
                                  cursor: 'pointer',
                                  padding: '2px 6px',
                                  background: 'var(--ant-color-fill-tertiary)',
                                  borderRadius: '4px',
                                  marginLeft: '4px'
                                }}>
                                  {task.cron_expression}
                                </code>
                              </CopyableTooltip>
                            </div>
                            <div>描述: {describeCronExpression(task.cron_expression)}</div>
                          </div>
                        )}
                      </Descriptions.Item>
                      <Descriptions.Item label="最大并发数">{task.max_instances}</Descriptions.Item>
                      <Descriptions.Item label="超时时间">{task.timeout_seconds}秒</Descriptions.Item>
                      <Descriptions.Item label="重试次数">{task.retry_count}</Descriptions.Item>
                      <Descriptions.Item label="重试延迟">{task.retry_delay}秒</Descriptions.Item>
                      <Descriptions.Item label="最后运行时间" span={2}>
                        {task.last_run_time ? formatDateTime(task.last_run_time) : '从未运行'}
                      </Descriptions.Item>
                      <Descriptions.Item label="下次运行时间" span={2}>
                        {task.next_run_time ? formatDateTime(task.next_run_time) : '无计划'}
                      </Descriptions.Item>
                      <Descriptions.Item label="创建时间">{formatDateTime(task.created_at)}</Descriptions.Item>
                      <Descriptions.Item label="更新时间">{formatDateTime(task.updated_at)}</Descriptions.Item>
                      <Descriptions.Item label="创建者" span={2}>
                        {task.created_by_username || `用户${task.created_by}`}
                      </Descriptions.Item>
                      <Descriptions.Item label="执行节点" span={2}>
                        {(() => {
                          const strategy = task.execution_strategy || task.project_execution_strategy

                          if (strategy === 'specified') {
                            return (
                              <Space>
                                <CloudServerOutlined style={{ color: '#1890ff' }} />
                                <span>{task.specified_worker_name || task.specified_worker_id}</span>
                                <Tag color="cyan">指定 Worker</Tag>
                              </Space>
                            )
                          }

                          if (strategy === 'fixed' || strategy === 'prefer') {
                            return (
                              <Space>
                                <CloudServerOutlined style={{ color: '#1890ff' }} />
                                <span>{task.project_bound_worker_name || task.project_bound_worker_id || '未绑定 Worker'}</span>
                                <Tag color="blue">绑定 Worker</Tag>
                              </Space>
                            )
                          }

                          if (strategy === 'auto') {
                            return (
                              <Space>
                                <CloudServerOutlined style={{ color: '#52c41a' }} />
                                <span>自动选择</span>
                                <Tag color="green">自动</Tag>
                              </Space>
                            )
                          }

                          return (
                            <Space>
                              <CloudServerOutlined style={{ color: '#52c41a' }} />
                              <span>自动选择</span>
                              <Tag color="green">自动</Tag>
                            </Space>
                          )
                        })()}
                      </Descriptions.Item>
                    </Descriptions>

                    {task.description && (
                      <Card title="任务描述" size="small" style={{ marginTop: 16 }}>
                        <p>{task.description}</p>
                      </Card>
                    )}

                    {task.execution_params && (
                      <Card title="执行参数" size="small" style={{ marginTop: 16 }}>
                        <pre style={{ background: token.colorFillQuaternary, padding: 12, borderRadius: 4, color: token.colorText }}>
                          {JSON.stringify(task.execution_params, null, 2)}
                        </pre>
                      </Card>
                    )}

                    {task.environment_vars && (
                      <Card title="环境变量" size="small" style={{ marginTop: 16 }}>
                        <pre style={{ background: token.colorFillQuaternary, padding: 12, borderRadius: 4, color: token.colorText }}>
                          {JSON.stringify(task.environment_vars, null, 2)}
                        </pre>
                      </Card>
                    )}
                  </Col>
                  
                  <Col span={8}>
                    <Row gutter={[16, 16]}>
                      <Col span={12}>
                        <Statistic
                          title="成功次数"
                          value={task.success_count ?? 0}
                          valueStyle={{ color: token.colorSuccess }}
                        />
                      </Col>
                      <Col span={12}>
                        <Statistic
                          title="失败次数"
                          value={task.failure_count ?? 0}
                          valueStyle={{ color: token.colorError }}
                        />
                      </Col>
                      <Col span={24}>
                        <Card title="成功率" size="small">
                          {(() => {
                            const success = task.success_count ?? 0
                            const failed = task.failure_count ?? 0
                            const totalRuns = success + failed
                            const percent =
                              totalRuns > 0 ? Math.round((success / totalRuns) * 100) : 0
                            const status = failed > success ? 'exception' : 'success'
                            return (
                          <Progress
                            percent={percent}
                            status={status}
                          />
                            )
                          })()}
                        </Card>
                      </Col>
                      {retryStats && retryStats.total_retries > 0 && (
                        <Col span={24}>
                          <Card 
                            title={
                              <Space>
                                <HistoryOutlined />
                                <span>重试统计</span>
                              </Space>
                            } 
                            size="small"
                          >
                            <Row gutter={[8, 8]}>
                              <Col span={12}>
                                <Statistic
                                  title="总重试次数"
                                  value={retryStats.total_retries}
                                  valueStyle={{ fontSize: 16 }}
                                />
                              </Col>
                              <Col span={12}>
                                <Statistic
                                  title="重试成功率"
                                  value={retryStats.retry_success_rate}
                                  suffix="%"
                                  valueStyle={{ 
                                    fontSize: 16,
                                    color: retryStats.retry_success_rate >= 50 ? token.colorSuccess : token.colorWarning
                                  }}
                                />
                              </Col>
                              <Col span={24}>
                                <div style={{ fontSize: 12, color: token.colorTextSecondary }}>
                                  {retryStats.retried_executions} 次执行触发了重试，
                                  平均每次重试 {retryStats.avg_retries_per_execution} 次
                                </div>
                              </Col>
                            </Row>
                          </Card>
                        </Col>
                      )}
                    </Row>
                  </Col>
                </Row>
              )
            },
            {
              key: 'executions',
              label: '执行记录',
              children: (
                <div>
                  <div style={{ marginBottom: 16 }}>
                    <Button
                      icon={<ReloadOutlined />}
                      onClick={loadExecutions}
                      loading={executionsLoading}
                    >
                      刷新
                    </Button>
                  </div>
                  
                  <ResponsiveTable
                    rowKey="id"
                    dataSource={executions}
                    columns={executionColumns}
                    loading={executionsLoading}
                    pagination={{
                      pageSize: 10,
                      showSizeChanger: true,
                      showQuickJumper: true
                    }}
                  />
                </div>
              )
            }
          ]}
        />
      </Card>
    </div>
  )
}

export default TaskDetail
