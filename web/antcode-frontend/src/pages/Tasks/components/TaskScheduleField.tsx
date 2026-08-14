import { DatePicker, Form, Input, InputNumber } from 'antd'

import type { ScheduleType } from '@/types'

interface TimedScheduleFieldProps {
  required: boolean
}

const IntervalScheduleField = () => (
  <Form.Item
    label="执行间隔(秒)"
    name="interval_seconds"
    rules={[{ required: true, message: '请输入执行间隔' }]}
  >
    <InputNumber min={60} max={86400} style={{ width: '100%' }} />
  </Form.Item>
)

const CronScheduleField = () => (
  <Form.Item
    label="Cron表达式"
    name="cron_expression"
    rules={[{ required: true, message: '请输入Cron表达式' }]}
  >
    <Input placeholder="例如: 0 0 * * *" />
  </Form.Item>
)

const TimedScheduleField = ({ required }: TimedScheduleFieldProps) => (
  <Form.Item
    label="执行时间"
    name="scheduled_time"
    rules={required ? [{ required: true, message: '请选择执行时间' }] : undefined}
  >
    <DatePicker showTime style={{ width: '100%' }} placeholder="请选择执行时间" />
  </Form.Item>
)

function renderScheduleField(scheduleType?: ScheduleType) {
  switch (scheduleType) {
    case 'interval':
      return <IntervalScheduleField />
    case 'cron':
      return <CronScheduleField />
    case 'date':
      return <TimedScheduleField required />
    case 'once':
      return <TimedScheduleField required={false} />
    default:
      return null
  }
}

const TaskScheduleField = () => (
  <Form.Item
    shouldUpdate={(previousValues, currentValues) =>
      previousValues.schedule_type !== currentValues.schedule_type
    }
  >
    {({ getFieldValue }) =>
      renderScheduleField(getFieldValue('schedule_type') as ScheduleType | undefined)
    }
  </Form.Item>
)

export default TaskScheduleField
