import React, { useState, useEffect, useCallback } from 'react'
import {
  Card,
  Form,
  Input,
  Select,
  Button,
  Space,
  Row,
  Col,
  InputNumber,
  Switch,
  DatePicker,
  Divider,
  Alert,
  Tag
} from 'antd'
import { ArrowLeftOutlined, SaveOutlined, CloudServerOutlined, DesktopOutlined } from '@ant-design/icons'
import { useNavigate, useSearchParams } from 'react-router-dom'
import { taskService } from '@/services/tasks'
import { projectService } from '@/services/projects'
import { nodeService } from '@/services/nodes'
import type { TaskCreateRequest, ScheduleType, Project, Node } from '@/types'
import { validateCronExpression } from '@/utils/cron'

const { Option, OptGroup } = Select
const { TextArea } = Input

const TaskCreate: React.FC = () => {
  const navigate = useNavigate()
  const [searchParams] = useSearchParams()
  const [form] = Form.useForm()
  const [loading, setLoading] = useState(false)
  const [projects, setProjects] = useState<Project[]>([])
  const [nodes, setNodes] = useState<Node[]>([])
  const [scheduleType, setScheduleType] = useState<ScheduleType>('once')

  // 从URL参数获取项目ID
  const projectIdFromUrl = searchParams.get('project_id')

  // 加载项目列表
  const loadProjects = useCallback(async () => {
    try {
      const response = await projectService.getProjects({ page: 1, size: 100 })
      setProjects(response.items)
      
      // 如果URL中有项目ID，设置为默认值
      if (projectIdFromUrl) {
        form.setFieldValue('project_id', projectIdFromUrl)
      }
    } catch {
      // 错误提示由拦截器统一处理
    }
  }, [form, projectIdFromUrl])

  // 加载可用节点列表
  const loadNodes = useCallback(async () => {
    try {
      const nodeList = await nodeService.getMyAvailableNodes()
      setNodes(nodeList)
    } catch {
      // 错误提示由拦截器统一处理
    }
  }, [])

  useEffect(() => {
    loadProjects()
    loadNodes()
  }, [loadProjects, loadNodes])

  // 处理表单提交
  const handleSubmit = async (values: TaskCreateRequest) => {
    setLoading(true)
    try {
      // 处理执行策略
      let executionStrategy: string | undefined
      let specifiedNodeId: string | undefined
      
      if (values.node_id === '__auto__') {
        // 自动选择策略
        executionStrategy = 'auto'
      } else if (values.node_id && values.node_id !== '') {
        // 指定节点策略
        executionStrategy = 'specified'
        specifiedNodeId = values.node_id
      }
      // 留空则继承项目配置（不设置 execution_strategy）

      const taskData: TaskCreateRequest = {
        name: values.name,
        description: values.description,
        project_id: values.project_id,
        schedule_type: values.schedule_type,
        cron_expression: values.cron_expression,
        interval_seconds: values.interval_seconds,
        scheduled_time: values.scheduled_time?.toISOString(),
        max_instances: values.max_instances || 1,
        timeout_seconds: values.timeout_seconds || 3600,
        retry_count: values.retry_count || 3,
        retry_delay: values.retry_delay || 60,
        execution_params: values.execution_params ? JSON.parse(values.execution_params) : undefined,
        environment_vars: values.environment_vars ? JSON.parse(values.environment_vars) : undefined,
        is_active: values.is_active !== false,
        execution_strategy: executionStrategy,
        specified_node_id: specifiedNodeId,
      }

      await taskService.createTask(taskData)
      navigate('/tasks')
    } catch {
      // 错误提示由拦截器统一处理
    } finally {
      setLoading(false)
    }
  }

  // 验证Cron表达式
  const validateCron = (_: unknown, value: string) => {
    if (scheduleType === 'cron' && value) {
      if (!validateCronExpression(value)) {
        return Promise.reject(new Error('请输入有效的Cron表达式'))
      }
    }
    return Promise.resolve()
  }

  // 验证JSON格式
  const validateJSON = (_: unknown, value: string) => {
    if (value) {
      try {
        JSON.parse(value)
      } catch {
        return Promise.reject(new Error('请输入有效的JSON格式'))
      }
    }
    return Promise.resolve()
  }

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
            <span>创建任务</span>
          </Space>
        }
      >
        <Form
          form={form}
          layout="vertical"
          onFinish={handleSubmit}
          initialValues={{
            schedule_type: 'once',
            max_instances: 1,
            timeout_seconds: 3600,
            retry_count: 3,
            retry_delay: 60,
            is_active: true,
            node_id: ''  // 默认继承项目配置
          }}
        >
          <Row gutter={24}>
            <Col span={12}>
              <Form.Item
                label="任务名称"
                name="name"
                rules={[
                  { required: true, message: '请输入任务名称' },
                  { min: 3, max: 255, message: '任务名称长度为3-255个字符' }
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
                <Select placeholder="请选择项目">
                  {projects.map(project => (
                    <Option key={project.id} value={project.id}>
                      {project.name} ({project.type})
                    </Option>
                  ))}
                </Select>
              </Form.Item>
            </Col>
          </Row>

          <Form.Item
            label="任务描述"
            name="description"
          >
            <TextArea
              rows={3}
              placeholder="请输入任务描述（可选）"
              maxLength={500}
              showCount
            />
          </Form.Item>

          <Divider>调度配置</Divider>

          <Row gutter={24}>
            <Col span={8}>
              <Form.Item
                label="调度类型"
                name="schedule_type"
                rules={[{ required: true, message: '请选择调度类型' }]}
              >
                <Select onChange={setScheduleType}>
                  <Option value="once">一次性执行</Option>
                  <Option value="interval">间隔执行</Option>
                  <Option value="cron">Cron表达式</Option>
                </Select>
              </Form.Item>
            </Col>
            <Col span={16}>
              {scheduleType === 'once' && (
                <Form.Item
                  label="执行时间"
                  name="scheduled_time"
                  rules={[{ required: true, message: '请选择执行时间' }]}
                >
                  <DatePicker
                    showTime
                    placeholder="请选择执行时间"
                    style={{ width: '100%' }}
                  />
                </Form.Item>
              )}
              
              {scheduleType === 'interval' && (
                <Form.Item
                  label="间隔时间（秒）"
                  name="interval_seconds"
                  rules={[
                    { required: true, message: '请输入间隔时间' },
                    { type: 'number', min: 1, message: '间隔时间必须大于0' }
                  ]}
                >
                  <InputNumber
                    placeholder="请输入间隔秒数"
                    style={{ width: '100%' }}
                    min={1}
                  />
                </Form.Item>
              )}
              
              {scheduleType === 'cron' && (
                <Form.Item
                  label="Cron表达式"
                  name="cron_expression"
                  rules={[
                    { required: true, message: '请输入Cron表达式' },
                    { validator: validateCron }
                  ]}
                  extra="格式: 秒 分 时 日 月 周，例如: 0 0 12 * * ? (每天12点执行)"
                >
                  <Input placeholder="请输入Cron表达式" />
                </Form.Item>
              )}
            </Col>
          </Row>

          <Divider>执行配置</Divider>

          <Alert
            type="info"
            showIcon
            message="任务执行策略"
            description="任务默认继承项目的执行策略配置。如需覆盖，可选择指定节点或自动选择。"
            style={{ marginBottom: 16 }}
          />

          <Row gutter={24}>
            <Col span={12}>
              <Form.Item
                label={
                  <Space>
                    <CloudServerOutlined />
                    <span>执行节点</span>
                  </Space>
                }
                name="node_id"
                tooltip="选择任务执行的节点。留空则继承项目配置；选择'自动选择'则由系统负载均衡选择最优节点"
              >
                <Select
                  placeholder="继承项目配置"
                  allowClear
                  showSearch
                  optionFilterProp="children"
                >
                  <Option value="">
                    <Space>
                      <DesktopOutlined style={{ color: '#1677ff' }} />
                      <span>继承项目配置</span>
                      <Tag color="default">推荐</Tag>
                    </Space>
                  </Option>
                  <Option value="__auto__">
                    <Space>
                      <CloudServerOutlined style={{ color: '#52c41a' }} />
                      <span>自动选择（负载均衡）</span>
                    </Space>
                  </Option>
                  {nodes.filter(n => n.status === 'online').length > 0 && (
                    <OptGroup label="指定节点">
                      {nodes.filter(n => n.status === 'online').map(node => (
                        <Option key={node.id} value={node.id}>
                          <Space>
                            <CloudServerOutlined style={{ color: '#13c2c2' }} />
                            <span>{node.name}</span>
                            {node.region && <Tag color="blue" style={{ marginLeft: 4 }}>{node.region}</Tag>}
                            {node.metrics && (
                              <span style={{ color: '#999', fontSize: 12 }}>
                                (CPU: {node.metrics.cpu?.toFixed(0)}% / 内存: {node.metrics.memory?.toFixed(0)}%)
                              </span>
                            )}
                          </Space>
                        </Option>
                      ))}
                    </OptGroup>
                  )}
                  {nodes.filter(n => n.status !== 'online').length > 0 && (
                    <OptGroup label="离线节点">
                      {nodes.filter(n => n.status !== 'online').map(node => (
                        <Option key={node.id} value={node.id} disabled>
                          <Space>
                            <CloudServerOutlined style={{ color: '#ff4d4f' }} />
                            <span style={{ color: '#999' }}>{node.name}</span>
                            <Tag color="error">离线</Tag>
                          </Space>
                        </Option>
                      ))}
                    </OptGroup>
                  )}
                </Select>
              </Form.Item>
            </Col>
            <Col span={6}>
              <Form.Item
                label="最大并发数"
                name="max_instances"
                rules={[
                  { required: true, message: '请输入最大并发数' },
                  { type: 'number', min: 1, max: 10, message: '并发数范围1-10' }
                ]}
              >
                <InputNumber min={1} max={10} style={{ width: '100%' }} />
              </Form.Item>
            </Col>
            <Col span={6}>
              <Form.Item
                label="超时时间（秒）"
                name="timeout_seconds"
                rules={[
                  { required: true, message: '请输入超时时间' },
                  { type: 'number', min: 1, message: '超时时间必须大于0' }
                ]}
              >
                <InputNumber min={1} style={{ width: '100%' }} />
              </Form.Item>
            </Col>
            <Col span={6}>
              <Form.Item
                label="重试次数"
                name="retry_count"
                rules={[
                  { required: true, message: '请输入重试次数' },
                  { type: 'number', min: 0, max: 10, message: '重试次数范围0-10' }
                ]}
              >
                <InputNumber min={0} max={10} style={{ width: '100%' }} />
              </Form.Item>
            </Col>
            <Col span={6}>
              <Form.Item
                label="重试延迟（秒）"
                name="retry_delay"
                rules={[
                  { required: true, message: '请输入重试延迟' },
                  { type: 'number', min: 1, message: '重试延迟必须大于0' }
                ]}
              >
                <InputNumber min={1} style={{ width: '100%' }} />
              </Form.Item>
            </Col>
          </Row>

          <Row gutter={24}>
            <Col span={12}>
              <Form.Item
                label="执行参数（JSON格式）"
                name="execution_params"
                rules={[{ validator: validateJSON }]}
              >
                <TextArea
                  rows={4}
                  placeholder='{"key": "value"}'
                />
              </Form.Item>
            </Col>
            <Col span={12}>
              <Form.Item
                label="环境变量（JSON格式）"
                name="environment_vars"
                rules={[{ validator: validateJSON }]}
              >
                <TextArea
                  rows={4}
                  placeholder='{"ENV_VAR": "value"}'
                />
              </Form.Item>
            </Col>
          </Row>

          <Form.Item
            label="启用任务"
            name="is_active"
            valuePropName="checked"
          >
            <Switch />
          </Form.Item>

          <Form.Item>
            <Space>
              <Button
                type="primary"
                htmlType="submit"
                loading={loading}
                icon={<SaveOutlined />}
              >
                创建任务
              </Button>
              <Button onClick={() => navigate('/tasks')}>
                取消
              </Button>
            </Space>
          </Form.Item>
        </Form>
      </Card>
    </div>
  )
}

export default TaskCreate
