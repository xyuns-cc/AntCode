import type React from 'react'
import { useState, useEffect, useCallback } from 'react'
import { useParams, useNavigate } from 'react-router'
import PageContainer from '@/components/common/PageContainer'
import {
  Card,
  Form,
  Input,
  Select,
  Button,
  Space,
  Row,
  Col,
  Switch,
  InputNumber,
  Skeleton,
  Typography,
  message,
} from 'antd'
import { ArrowLeftOutlined, SaveOutlined } from '@ant-design/icons'
import { useUpdateTask } from '@/hooks/api/useTasks'
import { taskService } from '@/services/tasks'
import { projectService } from '@/services/projects'
import Logger from '@/utils/logger'
import type { Task, Project, TaskUpdateRequest } from '@/types'
import useAuth from '@/hooks/useAuth'
import dayjs from 'dayjs'
import {
  buildTaskScheduleUpdate,
  TASK_SCHEDULE_OPTIONS,
  type TaskScheduleFormValues,
} from './taskScheduleUpdate'
import TaskScheduleField from './components/TaskScheduleField'
import { parseExecutionParams, parseEnvironmentVars } from './taskJsonFields'

const { Title } = Typography
const { Option } = Select
const { TextArea } = Input

interface TaskEditFormValues extends TaskScheduleFormValues {
  name: string
  description?: string
  max_instances: number
  timeout_seconds: number
  retry_count: number
  retry_delay: number
  is_active: boolean
  execution_params?: string
  environment_vars?: string
}

const TaskEdit: React.FC = () => {
  Logger.log('TaskEdit组件渲染')

  const { id } = useParams<{ id: string }>()
  const navigate = useNavigate()
  const { mutateAsync: updateTask } = useUpdateTask()
  const { isAuthenticated, loading: authLoading } = useAuth()
  const [form] = Form.useForm()
  const [task, setTask] = useState<Task | null>(null)
  const [projects, setProjects] = useState<Project[]>([])
  const [loading, setLoading] = useState(false)
  const [submitting, setSubmitting] = useState(false)

  Logger.log(
    'TaskEdit - id:',
    id,
    'isAuthenticated:',
    isAuthenticated,
    'authLoading:',
    authLoading,
    'task:',
    task,
    'loading:',
    loading
  )

  // 加载任务详情
  const loadTask = useCallback(async () => {
    if (!id) return

    setLoading(true)
    try {
      const taskData = await taskService.getTask(id)
      setTask(taskData)

      // 设置表单初始值
      form.setFieldsValue({
        name: taskData.name,
        description: taskData.description,
        project_id: taskData.project_id,
        schedule_type: taskData.schedule_type,
        cron_expression: taskData.cron_expression,
        interval_seconds: taskData.interval_seconds,
        scheduled_time: taskData.scheduled_time ? dayjs(taskData.scheduled_time) : null,
        max_instances: taskData.max_instances ?? 1,
        timeout_seconds: taskData.timeout_seconds ?? 3600,
        retry_count: taskData.retry_count ?? 3,
        retry_delay: taskData.retry_delay ?? 60,
        is_active: taskData.is_active,
        execution_params: taskData.execution_params
          ? JSON.stringify(taskData.execution_params, null, 2)
          : '',
        environment_vars: taskData.environment_vars
          ? JSON.stringify(taskData.environment_vars, null, 2)
          : '',
      })
    } catch {
      // 错误提示由拦截器统一处理
      navigate('/tasks')
    } finally {
      setLoading(false)
    }
  }, [id, form, navigate])

  // 加载项目列表
  const loadProjects = useCallback(async () => {
    try {
      const items = await projectService.getAllProjects()
      Logger.log('TaskEdit - 项目API响应:', items)
      setProjects(items)
    } catch {
      Logger.error('加载项目列表失败')
      message.error('加载项目列表失败')
    }
  }, [])

  // 处理表单提交
  const handleSubmit = async (values: TaskEditFormValues) => {
    if (!id || !task) return

    setSubmitting(true)
    try {
      const executionParams = parseExecutionParams(values.execution_params)
      if (!executionParams.ok) {
        message.error(executionParams.error)
        return
      }

      const environmentVars = parseEnvironmentVars(values.environment_vars)
      if (!environmentVars.ok) {
        message.error(environmentVars.error)
        return
      }

      const updateData: TaskUpdateRequest = {
        name: values.name,
        description: values.description,
        ...buildTaskScheduleUpdate(values),
        max_instances: values.max_instances,
        timeout_seconds: values.timeout_seconds,
        retry_count: values.retry_count,
        retry_delay: values.retry_delay,
        is_active: values.is_active,
        execution_params: executionParams.value,
        environment_vars: environmentVars.value,
      }

      await updateTask({ id, payload: updateData })
      navigate(`/tasks/${id}`)
    } catch {
      // 通知由拦截器统一处理
    } finally {
      setSubmitting(false)
    }
  }

  useEffect(() => {
    if (isAuthenticated && !authLoading && id) {
      loadTask()
      loadProjects()
    }
  }, [id, isAuthenticated, authLoading, loadTask, loadProjects])

  // 认证加载状态
  if (authLoading) {
    return (
      <div style={{ padding: '24px' }}>
        <Card>
          <Skeleton active paragraph={{ rows: 6 }} />
        </Card>
      </div>
    )
  }

  // 未认证状态
  if (!isAuthenticated) {
    return (
      <div style={{ textAlign: 'center', padding: '50px' }}>
        <Title level={4}>需要登录</Title>
        <Button type="primary" onClick={() => navigate('/login')}>
          去登录
        </Button>
      </div>
    )
  }

  if (loading) {
    return (
      <div style={{ padding: '24px' }}>
        <Card>
          <Skeleton active paragraph={{ rows: 8 }} />
        </Card>
      </div>
    )
  }

  if (!task) {
    return (
      <div style={{ textAlign: 'center', padding: '50px' }}>
        <Title level={4}>任务不存在</Title>
        <Button onClick={() => navigate('/tasks')}>返回任务列表</Button>
      </div>
    )
  }

  return (
    <PageContainer scrollable>
      {/* 页面头部 */}
      <Card
        title={
          <Space>
            <Button
              type="text"
              icon={<ArrowLeftOutlined />}
              onClick={() => navigate(`/tasks/${id}`)}
            >
              返回
            </Button>
            <span>编辑任务: {task.name}</span>
          </Space>
        }
        extra={
          <Space>
            <Button onClick={() => navigate(`/tasks/${id}`)}>取消</Button>
            <Button
              type="primary"
              icon={<SaveOutlined />}
              loading={submitting}
              onClick={() => form.submit()}
            >
              保存
            </Button>
          </Space>
        }
      >
        <Form form={form} layout="vertical" onFinish={handleSubmit}>
          <Row gutter={24}>
            <Col span={12}>
              <Form.Item
                label="任务名称"
                name="name"
                rules={[
                  { required: true, message: '请输入任务名称' },
                  { min: 3, max: 255, message: '任务名称长度为3-255个字符' },
                ]}
              >
                <Input placeholder="请输入任务名称" />
              </Form.Item>
            </Col>
            <Col span={12}>
              <Form.Item
                label="关联项目"
                name="project_id"
                rules={[{ required: true, message: '请选择关联项目' }]}
              >
                <Select placeholder="请选择项目" disabled>
                  {projects.map((project) => (
                    <Option key={project.id} value={project.id}>
                      {project.name} ({project.type})
                    </Option>
                  ))}
                </Select>
              </Form.Item>
            </Col>
          </Row>

          <Form.Item label="任务描述" name="description">
            <TextArea rows={3} placeholder="请输入任务描述" />
          </Form.Item>

          <Row gutter={24}>
            <Col span={8}>
              <Form.Item
                label="调度类型"
                name="schedule_type"
                rules={[{ required: true, message: '请选择调度类型' }]}
              >
                <Select placeholder="请选择调度类型">
                  {TASK_SCHEDULE_OPTIONS.map((option) => (
                    <Option key={option.value} value={option.value}>
                      {option.label}
                    </Option>
                  ))}
                </Select>
              </Form.Item>
            </Col>
            <Col span={8}>
              <Form.Item
                label="最大实例数"
                name="max_instances"
                rules={[{ required: true, message: '请输入最大实例数' }]}
              >
                <InputNumber min={1} max={10} style={{ width: '100%' }} />
              </Form.Item>
            </Col>
            <Col span={8}>
              <Form.Item
                label="超时时间(秒)"
                name="timeout_seconds"
                rules={[{ required: true, message: '请输入超时时间' }]}
              >
                <InputNumber min={60} max={86400} style={{ width: '100%' }} />
              </Form.Item>
            </Col>
          </Row>

          <Row gutter={24}>
            <Col span={8}>
              <Form.Item
                label="重试次数"
                name="retry_count"
                rules={[{ required: true, message: '请输入重试次数' }]}
              >
                <InputNumber min={0} max={10} style={{ width: '100%' }} />
              </Form.Item>
            </Col>
            <Col span={8}>
              <Form.Item
                label="重试延迟(秒)"
                name="retry_delay"
                rules={[{ required: true, message: '请输入重试延迟' }]}
              >
                <InputNumber min={1} max={3600} style={{ width: '100%' }} />
              </Form.Item>
            </Col>
            <Col span={8}>
              <Form.Item label="启用状态" name="is_active" valuePropName="checked">
                <Switch checkedChildren="启用" unCheckedChildren="禁用" />
              </Form.Item>
            </Col>
          </Row>

          <TaskScheduleField />

          <Row gutter={24}>
            <Col span={12}>
              <Form.Item label="执行参数 (JSON格式)" name="execution_params">
                <TextArea rows={6} placeholder='{"key": "value"}' />
              </Form.Item>
            </Col>
            <Col span={12}>
              <Form.Item label="环境变量 (JSON格式)" name="environment_vars">
                <TextArea rows={6} placeholder='{"ENV_VAR": "value"}' />
              </Form.Item>
            </Col>
          </Row>
        </Form>
      </Card>
    </PageContainer>
  )
}

export default TaskEdit
