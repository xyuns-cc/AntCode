import { CheckCircleOutlined, ThunderboltOutlined, WarningOutlined } from '@ant-design/icons'
import { Badge, Button, Card, Col, Row, Space, theme } from 'antd'
import type { ChartData, ChartOptions } from 'chart.js'
import { Line } from 'react-chartjs-2'
import { AlertIcon } from '../StatusIcons'
import type { MonitorAlert, PerformancePeriod } from '../types'

interface AlertsPerformanceSectionProps {
  alerts: MonitorAlert[]
  period: PerformancePeriod
  cpuData: ChartData<'line'>
  memoryData: ChartData<'line'>
  chartOptions: ChartOptions<'line'>
  onShowAllAlerts: () => void
  onPeriodChange: (period: PerformancePeriod) => void
}

const AlertList = ({ alerts, emptyHeight }: { alerts: MonitorAlert[]; emptyHeight: number | string }) => {
  const { token } = theme.useToken()
  if (alerts.length === 0) {
    return (
      <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', justifyContent: 'center', height: emptyHeight, color: token.colorTextSecondary }}>
        <CheckCircleOutlined style={{ fontSize: 40, marginBottom: 12, color: token.colorSuccess }} />
        <span style={{ fontSize: 13 }}>暂无告警，系统运行正常</span>
      </div>
    )
  }
  return (
    <>{alerts.map((alert) => (
      <div key={alert.id} className={`alert-item-compact alert-${alert.type}`}>
        <div className="alert-icon-compact"><AlertIcon type={alert.type} /></div>
        <div className="alert-details-compact">
          <div className="alert-header-compact">
            <span className="alert-title-compact">{alert.title}</span>
            <span className="alert-time-compact">{alert.time}</span>
          </div>
          <p className="alert-message-compact">{alert.message}</p>
        </div>
      </div>
    ))}</>
  )
}

const AlertsCard = ({ alerts, onShowAll }: { alerts: MonitorAlert[]; onShowAll: () => void }) => (
  <Card
    size="small"
    title={<span style={{ fontSize: 14 }}><WarningOutlined /> 资源告警 {alerts.length > 0 && <Badge count={alerts.length} style={{ marginLeft: 8 }} size="small" />}</span>}
    extra={alerts.length > 0 && <a style={{ fontSize: 11 }} onClick={onShowAll}>查看全部</a>}
    className="alerts-card"
    styles={{ body: { height: 360, overflow: 'auto', padding: alerts.length === 0 ? '16px' : undefined } }}
  >
    <div className="alerts-list"><AlertList alerts={alerts.slice(0, 5)} emptyHeight="100%" /></div>
  </Card>
)

const PeriodSelector = ({ period, onChange }: { period: PerformancePeriod; onChange: (period: PerformancePeriod) => void }) => (
  <Space.Compact size="small">
    {(['24h', '7d', '30d'] as const).map((value) => (
      <Button key={value} type={period === value ? 'primary' : 'default'} size="small" onClick={() => onChange(value)}>
        {value}
      </Button>
    ))}
  </Space.Compact>
)

const TrendChart = ({ title, data, options, background }: {
  title: string
  data: ChartData<'line'>
  options: ChartOptions<'line'>
  background: string
}) => {
  const { token } = theme.useToken()
  return (
    <div>
      <p style={{ fontSize: 12, marginBottom: 10, color: token.colorTextSecondary, fontWeight: 500 }}>{title}</p>
      <div style={{ height: 160, padding: '8px', borderRadius: '6px', background }}>
        <Line data={data} options={options} />
      </div>
    </div>
  )
}

const PerformanceCard = ({ period, cpuData, memoryData, options, onPeriodChange }: {
  period: PerformancePeriod
  cpuData: ChartData<'line'>
  memoryData: ChartData<'line'>
  options: ChartOptions<'line'>
  onPeriodChange: (period: PerformancePeriod) => void
}) => (
  <Card
    size="small"
    title={<span style={{ fontSize: 14 }}><ThunderboltOutlined /> 性能趋势</span>}
    extra={<PeriodSelector period={period} onChange={onPeriodChange} />}
    styles={{ body: { height: 360 } }}
  >
    <div style={{ marginBottom: 16 }}>
      <TrendChart title="集群CPU使用率" data={cpuData} options={options} background="rgba(24, 144, 255, 0.02)" />
    </div>
    <TrendChart title="集群内存使用率" data={memoryData} options={options} background="rgba(114, 46, 209, 0.02)" />
  </Card>
)

export const AlertsPerformanceSection = (props: AlertsPerformanceSectionProps) => (
  <Row gutter={12} style={{ marginTop: 16, marginBottom: 12 }}>
    <Col xs={24} lg={12}><AlertsCard alerts={props.alerts} onShowAll={props.onShowAllAlerts} /></Col>
    <Col xs={24} lg={12}>
      <PerformanceCard
        period={props.period}
        cpuData={props.cpuData}
        memoryData={props.memoryData}
        options={props.chartOptions}
        onPeriodChange={props.onPeriodChange}
      />
    </Col>
  </Row>
)

export { AlertList }
