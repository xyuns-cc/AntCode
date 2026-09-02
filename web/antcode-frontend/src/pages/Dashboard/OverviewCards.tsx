import { CheckCircleOutlined, CloudServerOutlined, ExclamationCircleOutlined, ProjectOutlined } from '@ant-design/icons'
import { Col, Row, theme } from 'antd'
import StatCard from '@/components/common/StatCard'
import type { DashboardStats } from '@/services/dashboard'
import type { WorkerAggregateStats } from '@/types'
import { NO_DATA, formatCount, formatPercent, formatRatio, type RecentTaskOutcome } from './present'

interface OverviewCardsProps {
  /** null = 这一块没拿到（/workers/stats 是管理员专属，普通用户 403）。 */
  workerStats: WorkerAggregateStats | null
  dashboardStats: DashboardStats | null
  /** null = 24 小时趋势没拿到。 */
  recent: RecentTaskOutcome | null
  loading: boolean
}

const OverviewCards = ({ workerStats, dashboardStats, recent, loading }: OverviewCardsProps) => {
  const { token } = theme.useToken()
  return (
    <Row gutter={[16, 16]} style={{ marginBottom: 20 }}>
      <Col xs={24} sm={12} lg={6}>
        <StatCard
          title="Worker 状态"
          value={formatRatio(workerStats?.onlineWorkers, workerStats?.totalWorkers)}
          subValue="当前在线Worker数"
          icon={<CloudServerOutlined />}
          iconColor={token.colorPrimary}
          loading={loading}
        />
      </Col>
      <Col xs={24} sm={12} lg={6}>
        <StatCard
          title="项目统计"
          value={formatRatio(dashboardStats?.projects.active, dashboardStats?.projects.total)}
          subValue="活跃/总项目数"
          icon={<ProjectOutlined />}
          iconColor={token.purple}
          loading={loading}
        />
      </Col>
      <Col xs={24} sm={12} lg={6}>
        {/* 数值与成功率同取 24 小时趋势，不再一个全时段一个当日（见 present.ts）。 */}
        <StatCard
          title="近24小时完成"
          value={formatCount(recent?.success)}
          subValue={recent ? `成功率 ${formatPercent(recent.successRate)}` : `成功率 ${NO_DATA}`}
          icon={<CheckCircleOutlined />}
          iconColor={token.colorSuccess}
          loading={loading}
        />
      </Col>
      <Col xs={24} sm={12} lg={6}>
        <StatCard
          title="近24小时异常"
          value={formatCount(recent?.failed)}
          subValue="需要关注的失败执行"
          icon={<ExclamationCircleOutlined />}
          iconColor={token.colorError}
          loading={loading}
        />
      </Col>
    </Row>
  )
}

export default OverviewCards
