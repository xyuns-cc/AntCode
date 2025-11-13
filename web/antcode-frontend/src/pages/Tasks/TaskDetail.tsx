import React, { useState, useEffect } from 'react'
import {
  Card,
  Descriptions,
  Button,
  Space,
  Tag,
  Tabs,
  Table,
  Progress,
  Alert,
  Statistic,
  Row,
  Col,
  Timeline,
  Empty
} from 'antd'
import {
  ArrowLeftOutlined,
  PlayCircleOutlined,
  PauseCircleOutlined,
  EditOutlined,
  DeleteOutlined,
  ReloadOutlined,
  EyeOutlined,
  FileTextOutlined
} from '@ant-design/icons'
import { useNavigate, useParams } from 'react-router-dom'
import { taskService } from '@/services/tasks'
import { logService } from '@/services/logs'
import LogViewer from '@/components/ui/LogViewer'
import type { Task, TaskExecution, TaskStatus, ScheduleType } from '@/types'
import { formatDateTime, formatDuration, formatStatus, formatTaskType } from '@/utils/format'
import { describeCronExpression } from '@/utils/cron'
import showNotification from '@/utils/notification'

const TaskDetail: React.FC = () => {
  const navigate = useNavigate()
  const { id } = useParams<{ id: string }>()
  const [task, setTask] = useState<Task | null>(null)
  const [executions, setExecutions] = useState<TaskExecution[]>([])
  const [loading, setLoading] = useState(false)
  const [executionsLoading, setExecutionsLoading] = useState(false)

  // 加载任务详情
  const loadTask = async () => {
    if (!id) return
    
    setLoading(true)
    try {
      const taskData = await taskService.getTask(parseInt(id))
      setTask(taskData)
    } catch (error: any) {
      // 错误提示由拦截器统一处理
    } finally {
      setLoading(false)
    }
  }

  // 加载执行记录
  const loadExecutions = async () => {
    if (!id) return
    
    setExecutionsLoading(true)
    try {
      const executionData = await taskService.getTaskExecutions(parseInt(id))
      setExecutions(executionData.items)
    } catch (error: any) {
      // 错误提示由拦截器统一处理
    } finally {
      setExecutionsLoading(false)
    }
  }

  useEffect(() => {
    loadTask()
    loadExecutions()
  }, [id])

  // 触发任务
  const handleTriggerTask = async () => {
    if (!task) return
    
    try {
      console.log('[DEBUG TaskDetail] handleTriggerTask click', { taskId: task.id })
      const resp = await taskService.triggerTask(task.id)
      console.log('[DEBUG TaskDetail] handleTriggerTask success resp', resp)
      if (resp?.message) {
        showNotification('success', resp.message)
      } else {
        showNotification('success', '任务已触发')
      }
      loadTask()
      loadExecutions()
    } catch (error: any) {
      console.log('[DEBUG TaskDetail] handleTriggerTask error', error)
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
    } catch (error: any) {
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
      dataIndex: 'execution_id',
      key: 'execution_id',
      render: (text: string) => (
        <code style={{ fontSize: '12px' }}>{text.substring(0, 8)}...</code>
      )
    },
    {
      title: '状态',
      dataIndex: 'status',
      key: 'status',
      render: (status: TaskStatus) => {
        const config = formatStatus(status)
        return <Tag color={config.color}>{config.text}</Tag>
      }
    },
    {
      title: '开始时间',
      dataIndex: 'start_time',
      key: 'start_time',
      render: (time: string) => formatDateTime(time)
    },
    {
      title: '结束时间',
      dataIndex: 'end_time',
      key: 'end_time',
      render: (time: string) => time ? formatDateTime(time) : '-'
    },
    {
      title: '持续时间',
      dataIndex: 'duration_seconds',
      key: 'duration_seconds',
      render: (duration: number) => duration ? formatDuration(duration) : '-'
    },
    {
      title: '退出码',
      dataIndex: 'exit_code',
      key: 'exit_code',
      render: (code: number) => code !== null ? code : '-'
    },
    {
      title: '重试次数',
      dataIndex: 'retry_count',
      key: 'retry_count'
    },
    {
      title: '操作',
      key: 'actions',
      render: (_, record: TaskExecution) => (
        <Button
          type="text"
          size="small"
          icon={<EyeOutlined />}
          onClick={() => navigate(`/tasks/${task.id}/executions/${record.execution_id}`)}
        >
          查看日志
        </Button>
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
                      <Descriptions.Item label="项目ID">{task.project_id}</Descriptions.Item>
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
                            <div>表达式: <code>{task.cron_expression}</code></div>
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
                      <Descriptions.Item label="创建者">
                        {task.created_by_username || `用户${task.created_by}`}
                      </Descriptions.Item>
                    </Descriptions>

                    {task.description && (
                      <Card title="任务描述" size="small" style={{ marginTop: 16 }}>
                        <p>{task.description}</p>
                      </Card>
                    )}

                    {task.execution_params && (
                      <Card title="执行参数" size="small" style={{ marginTop: 16 }}>
                        <pre style={{ background: '#f5f5f5', padding: 12, borderRadius: 4 }}>
                          {JSON.stringify(task.execution_params, null, 2)}
                        </pre>
                      </Card>
                    )}

                    {task.environment_vars && (
                      <Card title="环境变量" size="small" style={{ marginTop: 16 }}>
                        <pre style={{ background: '#f5f5f5', padding: 12, borderRadius: 4 }}>
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
                          value={task.success_count}
                          valueStyle={{ color: '#3f8600' }}
                        />
                      </Col>
                      <Col span={12}>
                        <Statistic
                          title="失败次数"
                          value={task.failure_count}
                          valueStyle={{ color: '#cf1322' }}
                        />
                      </Col>
                      <Col span={24}>
                        <Card title="成功率" size="small">
                          <Progress
                            percent={
                              task.success_count + task.failure_count > 0
                                ? Math.round((task.success_count / (task.success_count + task.failure_count)) * 100)
                                : 0
                            }
                            status={task.failure_count > task.success_count ? 'exception' : 'success'}
                          />
                        </Card>
                      </Col>
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
                  
                  <Table
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
