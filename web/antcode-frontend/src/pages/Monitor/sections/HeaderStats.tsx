import {
  ClockCircleOutlined,
  CloseCircleOutlined,
  CloudServerOutlined,
  SyncOutlined,
  ThunderboltOutlined,
  WarningOutlined,
} from '@ant-design/icons'
import { Badge, Button, Col, Row, theme } from 'antd'
import { getSystemStatus } from '../status'
import type { MonitorStats } from '../types'

interface HeaderStatsProps {
  currentTime: Date
  loading: boolean
  stats: MonitorStats
  onRefresh: () => void
}

const statItems = (stats: MonitorStats) => [
  { label: '执行 Worker', value: stats.totalWorkers, icon: <CloudServerOutlined />, color: 'primary' as const },
  { label: '运行任务', value: stats.totalTasks, icon: <ThunderboltOutlined />, color: 'info' as const },
  { label: '警告', value: stats.warningCount, icon: <WarningOutlined />, color: 'warning' as const },
  { label: '错误', value: stats.errorCount, icon: <CloseCircleOutlined />, color: 'error' as const },
]

export const HeaderStats = ({ currentTime, loading, stats, onRefresh }: HeaderStatsProps) => {
  const { token } = theme.useToken()
  const system = getSystemStatus(stats.systemStatus)
  return (
    <>
      <div className="monitor-header-simple">
        <div className="header-left">
          <h2 style={{ margin: 0, fontSize: '18px', fontWeight: 600 }}>
            <CloudServerOutlined style={{ marginRight: 8, color: token.colorPrimary }} />
            Worker监控
          </h2>
          <div className="header-badges">
            <Badge status={system.badge} text={system.text} />
            <span style={{ fontSize: 12, color: token.colorTextSecondary, marginLeft: 16 }}>
              <ClockCircleOutlined /> {currentTime.toLocaleString('zh-CN')}
            </span>
          </div>
        </div>
        <Button icon={<SyncOutlined spin={loading} />} onClick={onRefresh} loading={loading} size="small">
          刷新
        </Button>
      </div>
      <Row gutter={[12, 12]} style={{ marginBottom: 16 }}>
        {statItems(stats).map((item) => {
          const color = token[`color${item.color[0].toUpperCase()}${item.color.slice(1)}` as keyof typeof token] as string
          return (
            <Col key={item.label} xs={12} sm={6}>
              <div className="mini-stat-card">
                <div className="stat-decor" style={{ background: `${color}10` }} />
                <div className="stat-icon" style={{ background: `${color}20`, color }}>{item.icon}</div>
                <div className="stat-content">
                  <div className="stat-label">{item.label}</div>
                  <div className="stat-value">{item.value}</div>
                </div>
              </div>
            </Col>
          )
        })}
      </Row>
    </>
  )
}
