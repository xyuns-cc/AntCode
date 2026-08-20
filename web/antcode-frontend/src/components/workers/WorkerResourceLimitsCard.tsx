/**
 * "生效值 / 已配置值" 对照卡片。
 *
 * 从 WorkerResourceManagement 拆出来：这两组数字来源不同（执行面心跳 vs 控制面 DB），
 * 合并成一行显示就是上一版把 settings 兜底值当生效值报出去的那个形状。
 */
import type React from 'react'
import { Card, Col, Row, Statistic, Alert, theme } from 'antd'
import type { WorkerResourceLimits } from '@/types'
import { limitView, isLimitDiverged } from './workerLimitDisplay'

interface WorkerResourceLimitsCardProps {
  limits: WorkerResourceLimits
  configuredLimits: WorkerResourceLimits
}

const FIELDS = [
  { key: 'max_concurrent_tasks', title: '最大并发', suffix: '个' },
  { key: 'task_memory_limit_mb', title: '内存限制', suffix: 'MB' },
  { key: 'task_cpu_time_limit_sec', title: 'CPU 时限', suffix: '秒' }
] as const

const VALUE_STYLE = { fontSize: 16 }

const WorkerResourceLimitsCard: React.FC<WorkerResourceLimitsCardProps> = ({ limits, configuredLimits }) => {
  const { token } = theme.useToken()
  const diverged = FIELDS.filter((field) => isLimitDiverged(limits[field.key], configuredLimits[field.key]))

  return (
    <Card size="small" style={{ background: token.colorFillQuaternary, marginBottom: 16 }}>
      <Row gutter={16}>
        {FIELDS.map((field) => {
          const effective = limitView(limits[field.key], field.suffix)
          const configured = limitView(configuredLimits[field.key], field.suffix)
          return (
            <Col span={8} key={field.key}>
              <Statistic
                title={`${field.title}（生效）`}
                value={effective.value}
                suffix={effective.suffix}
                valueStyle={VALUE_STYLE}
              />
              <Statistic
                title={`${field.title}（已配置）`}
                value={configured.value}
                suffix={configured.suffix}
                valueStyle={VALUE_STYLE}
              />
            </Col>
          )
        })}
      </Row>
      {diverged.length > 0 && (
        <Alert
          style={{ marginTop: 12 }}
          type="warning"
          showIcon
          message="配置值未生效"
          description={`${diverged.map((field) => field.title).join('、')} 的下发值与 Worker 上报的生效值不一致：Worker 按自身内存预算重算或拒绝了该配置，以生效值为准。`}
        />
      )}
    </Card>
  )
}

export default WorkerResourceLimitsCard
