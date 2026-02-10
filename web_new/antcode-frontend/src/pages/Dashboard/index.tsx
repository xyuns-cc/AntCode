import React, { useEffect, useState, useRef, useCallback, memo } from 'react'
import { Row, Col, Card, Progress, Button, Tabs, Flex, Typography, Skeleton, theme, Tooltip } from 'antd'
import {
  ClusterOutlined, ProjectOutlined, CheckCircleOutlined, SyncOutlined, MonitorOutlined,
  ClockCircleOutlined, DashboardOutlined, BugOutlined, CloudServerOutlined,
  ExclamationCircleOutlined, PlayCircleOutlined, ThunderboltOutlined,
  FieldTimeOutlined, DatabaseOutlined, GlobalOutlined
} from '@ant-design/icons'
import { dashboardService, type DashboardStats, type SystemMetrics, type HourlyTrendItem } from '@/services/dashboard'
import { workerService } from '@/services/workers'
import type { WorkerAggregateStats, ClusterSpiderStats } from '@/types'
import SpiderStatsTab from '@/components/workers/SpiderStatsTab'

const MonitorTab = React.lazy(() => import('@/pages/Monitor'))
const { Title, Text } = Typography

// 自动刷新间隔（毫秒）
const AUTO_REFRESH_INTERVAL = 30000

// 统计卡片组件 - 与爬虫统计页面样式一致
interface StatCardProps {
  title: string
  value: string | number
  subValue?: string
  icon: React.ReactNode
  iconBg: string
  iconColor: string
  loading?: boolean
}

const StatCard: React.FC<StatCardProps> = memo(({ title, value, subValue, icon, iconColor, loading }) => {
  const { token } = theme.useToken()
  return (
    <Skeleton loading={loading} active paragraph={{ rows: 1 }}>
      <div
        style={{
          background: token.colorBgContainer,
          border: `1px solid ${token.colorBorderSecondary}`,
          borderRadius: 12,
          padding: '14px 16px',
          position: 'relative',
          overflow: 'hidden',
          height: 110,
          transition: 'border-color 0.2s ease, box-shadow 0.2s ease',
          cursor: 'pointer'
        }}
        onMouseEnter={(e) => {
          e.currentTarget.style.borderColor = iconColor
          e.currentTarget.style.boxShadow = `0 4px 12px ${iconColor}20`
        }}
        onMouseLeave={(e) => {
          e.currentTarget.style.borderColor = token.colorBorderSecondary
          e.currentTarget.style.boxShadow = 'none'
        }}
      >
        {/* 右上角装饰大圆 */}
        <div
          style={{
            position: 'absolute',
            right: -20,
            top: -20,
            width: 80,
            height: 80,
            borderRadius: '50%',
            background: `${iconColor}10`
          }}
        />
        {/* 右上角方形圆角图标 */}
        <div
          style={{
            position: 'absolute',
            right: 12,
            top: 12,
            width: 36,
            height: 36,
            borderRadius: 10,
            background: `${iconColor}20`,
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'center',
            fontSize: 16,
            color: iconColor,
            zIndex: 1
          }}
        >
          {icon}
        </div>
        {/* 内容区 */}
        <div style={{ paddingRight: 50, position: 'relative', zIndex: 1 }}>
          <Text type="secondary" style={{ fontSize: 12, display: 'block', marginBottom: 6 }}>
            {title}
          </Text>
          <span style={{ color: token.colorText, fontSize: 24, fontWeight: 600, lineHeight: 1 }}>{value}</span>
          {subValue && (
            <Text type="secondary" style={{ fontSize: 11, display: 'block', marginTop: 6 }}>
              {subValue}
            </Text>
          )}
        </div>
      </div>
    </Skeleton>
  )
})

// 资源进度条组件
interface ResourceBarProps {
  label: string
  value: number
  color: string
}

const ResourceBar: React.FC<ResourceBarProps> = memo(({ label, value, color }) => {
  const { token } = theme.useToken()
  return (
    <div style={{ marginBottom: 16 }}>
      <Flex justify="space-between" style={{ marginBottom: 6 }}>
        <Text style={{ fontSize: 13 }}>{label}</Text>
        <Text strong style={{ fontSize: 13 }}>{value}%</Text>
      </Flex>
      <Progress
        percent={value}
        showInfo={false}
        strokeColor={color}
        trailColor={token.colorFillSecondary}
        size="small"
      />
    </div>
  )
})

const Dashboard: React.FC = memo(() => {
  const { token } = theme.useToken()
  // 区分首次加载和后续刷新
  const [initialLoading, setInitialLoading] = useState(true)
  const [isRefreshing, setIsRefreshing] = useState(false)
  const [dashboardStats, setDashboardStats] = useState<DashboardStats | null>(null)
  const [systemMetrics, setSystemMetrics] = useState<SystemMetrics | null>(null)
  const [workerStats, setWorkerStats] = useState<WorkerAggregateStats | null>(null)
  const [spiderStats, setSpiderStats] = useState<ClusterSpiderStats | null>(null)
  const [hourlyTrend, setHourlyTrend] = useState<HourlyTrendItem[]>([])
  const [lastUpdated, setLastUpdated] = useState<Date | null>(null)
  const [activeTab, setActiveTab] = useState('overview')
  
  // 用于追踪是否已完成首次加载
  const isInitialLoadDone = useRef(false)

  const normalizePercent = (value: unknown): number => {
    const n = Number(value)
    return Number.isFinite(n) ? Math.min(100, Math.max(0, Math.round(n))) : 0
  }

  const cpuPercent = normalizePercent(systemMetrics?.cpu_usage?.percent)
  const memoryPercent = normalizePercent(systemMetrics?.memory_usage?.percent)
  const diskPercent = normalizePercent(systemMetrics?.disk_usage?.percent)

  // 无感刷新：后台静默获取数据
  const loadDashboardData = useCallback(async (silent = false) => {
    // 首次加载显示骨架屏，后续刷新静默进行
    if (!silent && !isInitialLoadDone.current) {
      setInitialLoading(true)
    } else if (!silent) {
      setIsRefreshing(true)
    }
    
    try {
      const [stats, metrics, workers, spider, trend] = await Promise.all([
        dashboardService.getDashboardStats(),
        dashboardService.getSystemMetrics(),
        workerService.getAggregateStats().catch(() => null),
        workerService.getClusterSpiderStats().catch(() => null),
        dashboardService.getHourlyTrend().catch(() => [])
      ])
      
      // 批量更新状态，减少重渲染
      setDashboardStats(stats)
      setSystemMetrics(metrics)
      setWorkerStats(workers)
      setSpiderStats(spider)
      setHourlyTrend(trend)
      setLastUpdated(new Date())
      
      // 标记首次加载完成
      if (!isInitialLoadDone.current) {
        isInitialLoadDone.current = true
      }
    } catch (e) {
      console.error('Failed to load dashboard:', e)
    } finally {
      setInitialLoading(false)
      setIsRefreshing(false)
    }
  }, [])

  // 手动刷新（显示刷新指示器）
  const handleManualRefresh = useCallback(() => {
    loadDashboardData(false)
  }, [loadDashboardData])

  // 首次加载
  useEffect(() => {
    loadDashboardData(false)
  }, [loadDashboardData])

  // 自动无感刷新
  useEffect(() => {
    const id = setInterval(() => {
      // 静默刷新，不显示任何加载状态
      loadDashboardData(true)
    }, AUTO_REFRESH_INTERVAL)
    return () => clearInterval(id)
  }, [loadDashboardData])

  const formatNumber = (n: number) => {
    if (n >= 1000000) return `${(n / 1000000).toFixed(1)}M`
    if (n >= 1000) return `${(n / 1000).toFixed(1)}K`
    return n.toString()
  }

  // 系统健康状态
  const healthStatus = dashboardStats?.system.status === 'normal' ? '健康' :
                       dashboardStats?.system.status === 'warning' ? '警告' : '异常'
  const healthColor = dashboardStats?.system.status === 'normal' ? token.colorSuccess :
                      dashboardStats?.system.status === 'warning' ? token.colorWarning : token.colorError

  return (
    <div>
      {/* 页头 */}
      <Flex justify="space-between" align="center" wrap="wrap" gap={16} style={{ marginBottom: 24 }}>
        <div>
          <Title level={4} style={{ margin: 0 }}>系统概览仪表板</Title>
          <Text type="secondary">实时监控分布式爬虫集群运行状态</Text>
        </div>
        <Flex align="center" gap={12} style={{
          background: token.colorBgContainer,
          padding: '8px 16px',
          borderRadius: 10,
          border: `1px solid ${token.colorBorderSecondary}`
        }}>
          <Flex align="center" gap={8}>
            <span style={{
              width: 10, height: 10, borderRadius: '50%',
              background: healthColor,
              boxShadow: `0 0 8px ${healthColor}`,
              animation: dashboardStats?.system.status === 'normal' ? 'pulse 2s infinite' : undefined
            }} />
            <Text strong style={{ fontSize: 13 }}>系统状态: {healthStatus}</Text>
          </Flex>
          <div style={{ width: 1, height: 16, background: token.colorBorderSecondary }} />
          {lastUpdated && (
            <Tooltip title="数据每30秒自动刷新">
              <Text type="secondary" style={{ fontSize: 12 }}>
                <ClockCircleOutlined style={{ marginRight: 4 }} />
                {lastUpdated.toLocaleTimeString()}
              </Text>
            </Tooltip>
          )}
          {activeTab === 'overview' && (
            <Button size="small" icon={<SyncOutlined spin={isRefreshing} />} onClick={handleManualRefresh} loading={isRefreshing}>
              刷新
            </Button>
          )}
        </Flex>
      </Flex>

      <Tabs
        activeKey={activeTab}
        onChange={setActiveTab}
        items={[
          {
            key: 'overview',
            label: <><DashboardOutlined /> 概览</>,
            children: (
              <Skeleton loading={initialLoading} active paragraph={{ rows: 12 }}>
                {/* 第一行：核心汇总指标 */}
                <Row gutter={[16, 16]} style={{ marginBottom: 20 }}>
                  <Col xs={24} sm={12} lg={6}>
                    <StatCard
                      title="Worker 状态"
                      value={`${workerStats?.onlineWorkers ?? 0} / ${workerStats?.totalWorkers ?? 0}`}
                      subValue="当前在线Worker数"
                      icon={<CloudServerOutlined />}
                      iconBg={`${token.colorPrimary}15`}
                      iconColor={token.colorPrimary}
                      loading={initialLoading}
                    />
                  </Col>
                  <Col xs={24} sm={12} lg={6}>
                    <StatCard
                      title="项目统计"
                      value={`${dashboardStats?.projects.active ?? 0} / ${dashboardStats?.projects.total ?? 0}`}
                      subValue="活跃/总项目数"
                      icon={<ProjectOutlined />}
                      iconBg={`${token.purple}15`}
                      iconColor={token.purple}
                      loading={initialLoading}
                    />
                  </Col>
                  <Col xs={24} sm={12} lg={6}>
                    <StatCard
                      title="今日完成任务"
                      value={formatNumber(dashboardStats?.tasks.success ?? 0)}
                      subValue={`成功率 ${systemMetrics?.success_rate?.toFixed(1) ?? 0}%`}
                      icon={<CheckCircleOutlined />}
                      iconBg={`${token.colorSuccess}15`}
                      iconColor={token.colorSuccess}
                      loading={initialLoading}
                    />
                  </Col>
                  <Col xs={24} sm={12} lg={6}>
                    <StatCard
                      title="今日异常"
                      value={dashboardStats?.tasks.failed ?? 0}
                      subValue="需要关注的失败任务"
                      icon={<ExclamationCircleOutlined />}
                      iconBg={`${token.colorError}15`}
                      iconColor={token.colorError}
                      loading={initialLoading}
                    />
                  </Col>
                </Row>

                {/* 第二行：任务状态 & 系统资源 */}
                <Row gutter={[16, 16]} style={{ marginBottom: 20 }}>
                  {/* 任务执行状态 */}
                  <Col xs={24} lg={16}>
                    <Card
                      title={<><PlayCircleOutlined style={{ marginRight: 8, color: token.colorTextSecondary }} />任务执行状态</>}
                      style={{ borderRadius: 16, height: '100%' }}
                      styles={{ body: { display: 'flex', flexDirection: 'column', height: 'calc(100% - 57px)' } }}
                    >
                      <Row gutter={[16, 16]}>
                        <Col xs={12} sm={6}>
                          <div style={{
                            background: token.colorFillQuaternary,
                            padding: 16,
                            borderRadius: 12,
                            textAlign: 'center',
                            border: `1px solid ${token.colorBorderSecondary}`
                          }}>
                            <Text type="secondary" style={{ fontSize: 11, textTransform: 'uppercase', letterSpacing: 1 }}>运行中</Text>
                            <div style={{ fontSize: 24, fontWeight: 700, color: token.colorInfo, marginTop: 4 }}>
                              {dashboardStats?.tasks.running ?? 0}
                            </div>
                          </div>
                        </Col>
                        <Col xs={12} sm={6}>
                          <div style={{
                            background: token.colorFillQuaternary,
                            padding: 16,
                            borderRadius: 12,
                            textAlign: 'center',
                            border: `1px solid ${token.colorBorderSecondary}`
                          }}>
                            <Text type="secondary" style={{ fontSize: 11, textTransform: 'uppercase', letterSpacing: 1 }}>队列中</Text>
                            <div style={{ fontSize: 24, fontWeight: 700, color: token.colorWarning, marginTop: 4 }}>
                              {systemMetrics?.queue_size ?? 0}
                            </div>
                          </div>
                        </Col>
                        <Col xs={12} sm={6}>
                          <div style={{
                            background: token.colorFillQuaternary,
                            padding: 16,
                            borderRadius: 12,
                            textAlign: 'center',
                            border: `1px solid ${token.colorBorderSecondary}`
                          }}>
                            <Text type="secondary" style={{ fontSize: 11, textTransform: 'uppercase', letterSpacing: 1 }}>今日成功</Text>
                            <div style={{ fontSize: 24, fontWeight: 700, color: token.colorSuccess, marginTop: 4 }}>
                              {formatNumber(dashboardStats?.tasks.success ?? 0)}
                            </div>
                          </div>
                        </Col>
                        <Col xs={12} sm={6}>
                          <div style={{
                            background: token.colorFillQuaternary,
                            padding: 16,
                            borderRadius: 12,
                            textAlign: 'center',
                            border: `1px solid ${token.colorBorderSecondary}`
                          }}>
                            <Text type="secondary" style={{ fontSize: 11, textTransform: 'uppercase', letterSpacing: 1 }}>今日失败</Text>
                            <div style={{ fontSize: 24, fontWeight: 700, color: token.colorError, marginTop: 4 }}>
                              {dashboardStats?.tasks.failed ?? 0}
                            </div>
                          </div>
                        </Col>
                      </Row>

                      {/* 24小时任务趋势图 */}
                      <div style={{ marginTop: 16, flex: 1, display: 'flex', flexDirection: 'column' }}>
                        <div style={{ display: 'flex', alignItems: 'flex-end', gap: 3, flex: 1, minHeight: 120 }}>
                          {(() => {
                            // 使用真实的24小时趋势数据
                            const data = hourlyTrend.length > 0 ? hourlyTrend : Array.from({ length: 24 }, (_, i) => ({ hour: i, tasks: 0, success: 0, failed: 0 }))
                            const maxTasks = Math.max(...data.map(d => d.tasks), 1)
                            return data.map((item, i) => {
                              const heightPercent = maxTasks > 0 ? Math.max((item.tasks / maxTasks) * 100, 5) : 5
                              return (
                                <Tooltip
                                  key={i}
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
                                      height: `${heightPercent}%`,
                                      background: `${token.colorPrimary}40`,
                                      borderRadius: '3px 3px 0 0',
                                      transition: 'all 0.2s ease',
                                      cursor: 'pointer',
                                      transformOrigin: 'bottom'
                                    }}
                                    onMouseEnter={(e) => {
                                      e.currentTarget.style.background = token.colorPrimary
                                      e.currentTarget.style.transform = 'scaleY(1.05)'
                                    }}
                                    onMouseLeave={(e) => {
                                      e.currentTarget.style.background = `${token.colorPrimary}40`
                                      e.currentTarget.style.transform = 'scaleY(1)'
                                    }}
                                  />
                                </Tooltip>
                              )
                            })
                          })()}
                        </div>
                        {/* X轴时间标签 */}
                        <Flex justify="space-between" style={{ padding: '0 2px', marginTop: 4 }}>
                          <Text type="secondary" style={{ fontSize: 10 }}>00:00</Text>
                          <Text type="secondary" style={{ fontSize: 10 }}>06:00</Text>
                          <Text type="secondary" style={{ fontSize: 10 }}>12:00</Text>
                          <Text type="secondary" style={{ fontSize: 10 }}>18:00</Text>
                          <Text type="secondary" style={{ fontSize: 10 }}>24:00</Text>
                        </Flex>
                        <Text type="secondary" style={{ fontSize: 11, display: 'block', textAlign: 'center', marginTop: 4 }}>
                          过去 24 小时任务处理趋势（单位：任务数/小时）
                        </Text>
                      </div>
                    </Card>
                  </Col>

                  {/* Master 资源监控 */}
                  <Col xs={24} lg={8}>
                    <Card
                      title={<><ThunderboltOutlined style={{ marginRight: 8, color: token.colorTextSecondary }} />Master 资源负载</>}
                      style={{ borderRadius: 16, height: '100%' }}
                    >
                      <ResourceBar label="CPU 使用率" value={cpuPercent} color={token.colorPrimary} />
                      <ResourceBar label="内存 使用率" value={memoryPercent} color={token.purple} />
                      <ResourceBar label="磁盘 占用率" value={diskPercent} color={token.colorWarning} />

                      <div style={{
                        marginTop: 16,
                        padding: 12,
                        borderRadius: 12,
                        background: token.colorFillQuaternary,
                        border: `1px solid ${token.colorBorderSecondary}`
                      }}>
                        <Flex align="center" gap={12}>
                          <div style={{
                            width: 40, height: 40, borderRadius: '50%',
                            background: token.colorBgContainer,
                            display: 'flex', alignItems: 'center', justifyContent: 'center',
                            boxShadow: `0 2px 8px ${token.colorBorderSecondary}`
                          }}>
                            <ClusterOutlined style={{ fontSize: 18, color: token.colorTextSecondary }} />
                          </div>
                          <div>
                            <Text type="secondary" style={{ fontSize: 12 }}>存储Worker连接状态</Text>
                            <div style={{ fontSize: 13, fontWeight: 600 }}>
                              {workerStats?.onlineWorkers ?? 0}/{workerStats?.totalWorkers ?? 0} Worker 就绪
                            </div>
                          </div>
                        </Flex>
                      </div>
                    </Card>
                  </Col>
                </Row>

                {/* 第三行：爬虫核心业务指标 */}
                <Card
                  title={<><GlobalOutlined style={{ marginRight: 8, color: token.colorTextSecondary }} />爬虫核心性能指标</>}
                  style={{ borderRadius: 16 }}
                >
                  <Row gutter={[32, 24]}>
                    <Col xs={24} sm={12} lg={6}>
                      <Flex vertical>
                        <Text type="secondary" style={{ fontSize: 13, marginBottom: 4 }}>
                          <ThunderboltOutlined style={{ marginRight: 4 }} />今日请求总数
                        </Text>
                        <span style={{ fontSize: 28, fontWeight: 700 }}>
                          {formatNumber(spiderStats?.totalRequests ?? 0)}
                        </span>
                        <Text type="success" style={{ fontSize: 12, marginTop: 4 }}>
                          ↑ 实时统计
                        </Text>
                      </Flex>
                    </Col>
                    <Col xs={24} sm={12} lg={6}>
                      <Flex vertical>
                        <Text type="secondary" style={{ fontSize: 13, marginBottom: 4 }}>
                          <CheckCircleOutlined style={{ marginRight: 4 }} />平均成功率
                        </Text>
                        <Flex align="baseline" gap={8}>
                          <span style={{ fontSize: 28, fontWeight: 700 }}>
                            {spiderStats?.totalResponses ? (((spiderStats.totalResponses - spiderStats.totalErrors) / spiderStats.totalResponses) * 100).toFixed(1) : '0'}%
                          </span>
                          <span style={{
                            fontSize: 11,
                            padding: '2px 6px',
                            borderRadius: 4,
                            background: `${token.colorSuccess}20`,
                            color: token.colorSuccess
                          }}>
                            良好
                          </span>
                        </Flex>
                        <Progress
                          percent={spiderStats?.totalResponses ? Math.round(((spiderStats.totalResponses - spiderStats.totalErrors) / spiderStats.totalResponses) * 100) : 0}
                          showInfo={false}
                          strokeColor={token.colorSuccess}
                          size="small"
                          style={{ marginTop: 8 }}
                        />
                      </Flex>
                    </Col>
                    <Col xs={24} sm={12} lg={6}>
                      <Flex vertical>
                        <Text type="secondary" style={{ fontSize: 13, marginBottom: 4 }}>
                          <DatabaseOutlined style={{ marginRight: 4 }} />累计抓取数据
                        </Text>
                        <span style={{ fontSize: 28, fontWeight: 700 }}>
                          {formatNumber(spiderStats?.totalItemsScraped ?? 0)}
                        </span>
                        <Text type="secondary" style={{ fontSize: 12, marginTop: 4 }}>
                          数据项
                        </Text>
                      </Flex>
                    </Col>
                    <Col xs={24} sm={12} lg={6}>
                      <Flex vertical>
                        <Text type="secondary" style={{ fontSize: 13, marginBottom: 4 }}>
                          <FieldTimeOutlined style={{ marginRight: 4 }} />平均响应延迟
                        </Text>
                        <span style={{ fontSize: 28, fontWeight: 700, color: token.colorPrimary }}>
                          {spiderStats?.avgLatencyMs?.toFixed(0) ?? 0} ms
                        </span>
                        <Flex gap={2} style={{ marginTop: 8 }}>
                          {[3, 4, 3, 5, 2, 4, 6, 3].map((h, i) => (
                            <div key={i} style={{ flex: 1, height: 6, borderRadius: 3, overflow: 'hidden', background: `${token.colorPrimary}20` }}>
                              <div style={{ width: `${h * 15}%`, height: '100%', background: token.colorPrimary }} />
                            </div>
                          ))}
                        </Flex>
                      </Flex>
                    </Col>
                  </Row>
                </Card>
              </Skeleton>
            )
          },
          {
            key: 'spider',
            label: <><BugOutlined /> 爬虫统计</>,
            children: (
              <React.Suspense fallback={<Skeleton active paragraph={{ rows: 6 }} />}>
                <SpiderStatsTab refreshKey={lastUpdated?.getTime()} />
              </React.Suspense>
            )
          },
          {
            key: 'monitor',
            label: <><MonitorOutlined /> 监控中心</>,
            children: (
              <React.Suspense fallback={<Skeleton active paragraph={{ rows: 6 }} />}>
                <MonitorTab />
              </React.Suspense>
            )
          }
        ]}
      />

      <style>{`
        @keyframes pulse {
          0%, 100% { opacity: 1; }
          50% { opacity: 0.5; }
        }
      `}</style>
    </div>
  )
})

export default Dashboard
