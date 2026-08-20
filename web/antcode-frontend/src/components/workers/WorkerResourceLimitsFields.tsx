/**
 * 资源限制配置的四个表单项。
 *
 * 从 WorkerResourceManagement 拆出来只为把主文件压回 300 行以内；必须渲染在
 * <Form> 内部才能拿到 form context。取值范围与后端 _validate_resource_params
 * (services/web_api/.../workers_resources.py) 一一对应，改一边就要改另一边。
 */
import type React from 'react'
import { Col, Form, InputNumber, Row, Space, Switch } from 'antd'
import { ThunderboltOutlined, DatabaseOutlined, ClockCircleOutlined, SyncOutlined } from '@ant-design/icons'

const CONCURRENT = { min: 1, max: 20 }
const MEMORY_MB = { min: 256, max: 8192, step: 256 }
const CPU_SEC = { min: 60, max: 3600, step: 60 }
const FULL_WIDTH = { width: '100%' }

const WorkerResourceLimitsFields: React.FC = () => (
  <>
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
          rules={[{ required: true }, { type: 'number', ...CONCURRENT }]}
        >
          <InputNumber {...CONCURRENT} style={FULL_WIDTH} addonAfter="个" />
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
          rules={[{ required: true }, { type: 'number', min: MEMORY_MB.min, max: MEMORY_MB.max }]}
        >
          <InputNumber {...MEMORY_MB} style={FULL_WIDTH} addonAfter="MB" />
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
          rules={[{ required: true }, { type: 'number', min: CPU_SEC.min, max: CPU_SEC.max }]}
        >
          <InputNumber {...CPU_SEC} style={FULL_WIDTH} addonAfter="秒" />
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
  </>
)

export default WorkerResourceLimitsFields
