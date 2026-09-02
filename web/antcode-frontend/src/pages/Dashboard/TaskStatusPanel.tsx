import { PlayCircleOutlined } from '@ant-design/icons'
import { Card, Col, Empty, Flex, Row, Tooltip, Typography, theme } from 'antd'
import type { GlobalToken } from 'antd/es/theme/interface'
import type { DashboardStats, HourlyTrendItem, SystemMetrics } from '@/services/dashboard'
import { formatCount, type RecentTaskOutcome } from './present'

const { Text } = Typography

// 空桶也要有可见高度，否则「0 个任务」的小时会整根消失，看起来像那一格没数据。
const MIN_BAR_PERCENT = 5

interface TaskStatusPanelProps {
  dashboardStats: DashboardStats | null
  systemMetrics: SystemMetrics | null
  recent: RecentTaskOutcome | null
  /** null = 24 小时趋势没拿到；空数组是后端不会返回的形状。 */
  hourlyTrend: HourlyTrendItem[] | null
}

const StatBox = ({ label, value, color, token }: { label: string; value: string; color: string; token: GlobalToken }) => (
  <Col xs={12} sm={6}>
    <div style={{
      background: token.colorFillQuaternary,
      padding: 16,
      borderRadius: 12,
      textAlign: 'center',
      border: `1px solid ${token.colorBorderSecondary}`
    }}>
      <Text type="secondary" style={{ fontSize: 11, textTransform: 'uppercase', letterSpacing: 1 }}>{label}</Text>
      <div style={{ fontSize: 24, fontWeight: 700, color, marginTop: 4 }}>{value}</div>
    </div>
  </Col>
)

const TrendBar = ({ item, maxTasks, token }: { item: HourlyTrendItem; maxTasks: number; token: GlobalToken }) => (
  <Tooltip
    title={
      <div style={{ textAlign: 'center' }}>
        <div style={{ fontWeight: 600 }}>{`${item.hour.toString().padStart(2, '0')}:00`}</div>
        <div>{`${item.tasks} 个任务`}</div>
        <div style={{ color: token.colorSuccess }}>{`成功: ${item.success}`}</div>
        <div style={{ color: token.colorError }}>{`失败: ${item.failed}`}</div>
      </div>
    }
    placement="top"
  >
    <div
      style={{
        flex: 1,
        height: `${Math.max((item.tasks / maxTasks) * 100, MIN_BAR_PERCENT)}%`,
        background: `${token.colorPrimary}40`,
        borderRadius: '3px 3px 0 0',
        transition: 'all 0.2s ease',
        cursor: 'pointer',
        transformOrigin: 'bottom'
      }}
      onMouseEnter={(event) => {
        event.currentTarget.style.background = token.colorPrimary
        event.currentTarget.style.transform = 'scaleY(1.05)'
      }}
      onMouseLeave={(event) => {
        event.currentTarget.style.background = `${token.colorPrimary}40`
        event.currentTarget.style.transform = 'scaleY(1)'
      }}
    />
  </Tooltip>
)

const HourlyTrend = ({ trend, token }: { trend: HourlyTrendItem[] | null; token: GlobalToken }) => {
  // 拿不到就说拿不到。以前这里补 24 个零桶，画出来是一条平的空趋势线，
  // 与「过去 24 小时真的一个任务都没跑」完全同形。
  if (trend === null) {
    return (
      <Flex align="center" justify="center" style={{ flex: 1, minHeight: 120 }}>
        <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="24 小时趋势未获取到" />
      </Flex>
    )
  }
  const maxTasks = Math.max(...trend.map((item) => item.tasks), 1)
  return (
    <>
      <div style={{ display: 'flex', alignItems: 'flex-end', gap: 3, flex: 1, minHeight: 120 }}>
        {trend.map((item, index) => (
          <TrendBar key={index} item={item} maxTasks={maxTasks} token={token} />
        ))}
      </div>
      <Flex justify="space-between" style={{ padding: '0 2px', marginTop: 4 }}>
        {['00:00', '06:00', '12:00', '18:00', '24:00'].map((label) => (
          <Text key={label} type="secondary" style={{ fontSize: 10 }}>{label}</Text>
        ))}
      </Flex>
    </>
  )
}

const TaskStatusPanel = ({ dashboardStats, systemMetrics, recent, hourlyTrend }: TaskStatusPanelProps) => {
  const { token } = theme.useToken()
  return (
    <Card
      title={<><PlayCircleOutlined style={{ marginRight: 8, color: token.colorTextSecondary }} />任务执行状态</>}
      style={{ borderRadius: 16, height: '100%' }}
      styles={{ body: { display: 'flex', flexDirection: 'column', height: 'calc(100% - 57px)' } }}
    >
      <Row gutter={[16, 16]}>
        <StatBox label="运行中" value={formatCount(dashboardStats?.tasks.running)} color={token.colorInfo} token={token} />
        {/* queue_size 只在 /dashboard/metrics 里，普通用户 403 —— 没拿到就是没拿到，不是队列空了。 */}
        <StatBox label="队列中" value={formatCount(systemMetrics?.queue_size)} color={token.colorWarning} token={token} />
        <StatBox label="近24小时成功" value={formatCount(recent?.success)} color={token.colorSuccess} token={token} />
        <StatBox label="近24小时失败" value={formatCount(recent?.failed)} color={token.colorError} token={token} />
      </Row>
      <div style={{ marginTop: 16, flex: 1, display: 'flex', flexDirection: 'column' }}>
        <HourlyTrend trend={hourlyTrend} token={token} />
        <Text type="secondary" style={{ fontSize: 11, display: 'block', textAlign: 'center', marginTop: 4 }}>
          过去 24 小时任务处理趋势（单位：任务数/小时）
        </Text>
      </div>
    </Card>
  )
}

export default TaskStatusPanel
