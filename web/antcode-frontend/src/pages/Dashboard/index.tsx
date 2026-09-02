import React, { useEffect, useState, useRef, useCallback, memo } from 'react'
import { Row, Col, Card, Progress, Button, Tabs, Flex, Typography, Skeleton, theme, Tooltip, Alert } from 'antd'
import {
  ClusterOutlined, CheckCircleOutlined, SyncOutlined, MonitorOutlined,
  ClockCircleOutlined, DashboardOutlined, BugOutlined,
  ThunderboltOutlined,
  FieldTimeOutlined, DatabaseOutlined, GlobalOutlined
} from '@ant-design/icons'
import { dashboardService, systemHealthFromMetrics, type DashboardStats, type SystemMetrics, type HourlyTrendItem } from '@/services/dashboard'
import { workerService } from '@/services/workers'
import type { WorkerAggregateStats, ClusterSpiderStats } from '@/types'
import SpiderStatsTab from '@/components/workers/SpiderStatsTab'
import PageContainer from '@/components/common/PageContainer'
import { successRateView } from '@/utils/spiderSuccessRate'
import OverviewCards from './OverviewCards'
import ResourceBar from './ResourceBar'
import TaskStatusPanel from './TaskStatusPanel'
import { NO_DATA, formatCount, formatRatio, summarizeRecentTasks } from './present'

const MonitorTab = React.lazy(() => import('@/pages/Monitor'))
const { Title, Text } = Typography

// 自动刷新间隔（毫秒）
const AUTO_REFRESH_INTERVAL = 30000

const Dashboard: React.FC = memo(() => {
  const { token } = theme.useToken()
  // 区分首次加载和后续刷新
  const [initialLoading, setInitialLoading] = useState(true)
  const [isRefreshing, setIsRefreshing] = useState(false)
  const [dashboardStats, setDashboardStats] = useState<DashboardStats | null>(null)
  const [systemMetrics, setSystemMetrics] = useState<SystemMetrics | null>(null)
  const [workerStats, setWorkerStats] = useState<WorkerAggregateStats | null>(null)
  const [spiderStats, setSpiderStats] = useState<ClusterSpiderStats | null>(null)
  // null = 还没拿到（首次加载中，或这一路请求失败）。空数组是后端不会返回的形状，
  // 拿它当"没数据"会和"24 小时里真的一个任务都没跑"混为一谈。
  const [hourlyTrend, setHourlyTrend] = useState<HourlyTrendItem[] | null>(null)
  const [lastUpdated, setLastUpdated] = useState<Date | null>(null)
  const [activeTab, setActiveTab] = useState('overview')
  const [loadError, setLoadError] = useState<string | null>(null)
  
  // 用于追踪是否已完成首次加载
  const isInitialLoadDone = useRef(false)

  // 采不到就是 null：以前非有限值兜成 0，管理员之外的用户看到的是「CPU 0%」的满绿面板。
  const normalizePercent = (value: unknown): number | null => {
    const n = Number(value)
    return Number.isFinite(n) ? Math.min(100, Math.max(0, Math.round(n))) : null
  }

  const cpuPercent = normalizePercent(systemMetrics?.cpu_usage?.percent)
  const memoryPercent = normalizePercent(systemMetrics?.memory_usage?.percent)
  const diskPercent = normalizePercent(systemMetrics?.disk_usage?.percent)
  const spiderSuccess = successRateView(spiderStats, token)
  const recentTasks = hourlyTrend === null ? null : summarizeRecentTasks(hourlyTrend)

  // 无感刷新：后台静默获取数据
  const loadDashboardData = useCallback(async (silent = false) => {
    // 首次加载显示骨架屏，后续刷新静默进行
    if (!silent && !isInitialLoadDone.current) {
      setInitialLoading(true)
    } else if (!silent) {
      setIsRefreshing(true)
    }
    
    try {
      // 摘要和管理员专属系统指标必须独立发起；普通用户的 metrics 403
      // 不能阻断其项目和任务统计。
      const metricsPromise = dashboardService.getSystemMetrics()
      const [statsResult, metricsResult, workersResult, spiderResult, trendResult] = await Promise.allSettled([
        dashboardService.getDashboardStats(),
        metricsPromise,
        workerService.getAggregateStats(),
        workerService.getClusterSpiderStats(),
        dashboardService.getHourlyTrend()
      ])

      const results = [statsResult, metricsResult, workersResult, spiderResult, trendResult]
      const anySuccess = results.some((r) => r.status === 'fulfilled')
      const failures = results.filter((r) => r.status === 'rejected') as PromiseRejectedResult[]

      if (statsResult.status === 'fulfilled') setDashboardStats(statsResult.value)
      if (metricsResult.status === 'fulfilled') setSystemMetrics(metricsResult.value)
      if (workersResult.status === 'fulfilled') setWorkerStats(workersResult.value)
      if (spiderResult.status === 'fulfilled') setSpiderStats(spiderResult.value)
      if (trendResult.status === 'fulfilled') setHourlyTrend(trendResult.value)

      if (failures.length > 0) {
        for (const failure of failures) console.warn('Dashboard partial failure:', failure.reason)
      }

      if (anySuccess) {
        setLastUpdated(new Date())
        setLoadError(null)
      } else {
        const first = failures[0]?.reason
        setLoadError(first instanceof Error ? first.message : '加载仪表盘数据失败')
      }

      // 标记首次加载完成
      if (!isInitialLoadDone.current) {
        isInitialLoadDone.current = true
      }
    } catch (e) {
      console.error('Failed to load dashboard:', e)
      setLoadError(e instanceof Error ? e.message : '加载仪表盘数据失败')
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

  // 系统健康状态。取的是真的 systemMetrics：之前读的 dashboardStats.system.status
  // 由 getDashboardStats 恒定填 'normal'（阈值函数从没被调用过），于是 CPU 打满也报「健康」。
  const systemHealth = systemHealthFromMetrics(systemMetrics)
  const healthStatus = { normal: '健康', warning: '警告', error: '异常', unknown: '未知' }[systemHealth]
  const healthColor = {
    normal: token.colorSuccess,
    warning: token.colorWarning,
    error: token.colorError,
    unknown: token.colorTextSecondary,
  }[systemHealth]

  return (
    <PageContainer scrollable>
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
              animation: systemHealth === 'normal' ? 'pulse 2s infinite' : undefined
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

      {loadError && (
        <Alert
          type="error"
          showIcon
          message="仪表盘数据加载失败"
          description={loadError}
          style={{ marginBottom: 16 }}
        />
      )}

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
                <OverviewCards
                  workerStats={workerStats}
                  dashboardStats={dashboardStats}
                  recent={recentTasks}
                  loading={initialLoading}
                />

                {/* 第二行：任务状态 & 系统资源 */}
                <Row gutter={[16, 16]} style={{ marginBottom: 20 }}>
                  {/* 任务执行状态 */}
                  <Col xs={24} lg={16}>
                    <TaskStatusPanel
                      dashboardStats={dashboardStats}
                      systemMetrics={systemMetrics}
                      recent={recentTasks}
                      hourlyTrend={hourlyTrend}
                    />
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
                              {formatRatio(workerStats?.onlineWorkers, workerStats?.totalWorkers)} Worker 就绪
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
                          <ThunderboltOutlined style={{ marginRight: 4 }} />请求总数
                        </Text>
                        <span style={{ fontSize: 28, fontWeight: 700 }}>
                          {formatCount(spiderStats?.totalRequests)}
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
                            {spiderSuccess.text}
                          </span>
                          <span style={{
                            fontSize: 11,
                            padding: '2px 6px',
                            borderRadius: 4,
                            background: `${spiderSuccess.color}20`,
                            color: spiderSuccess.color
                          }}>
                            {spiderSuccess.label}
                          </span>
                        </Flex>
                        <Progress
                          percent={spiderSuccess.percent}
                          showInfo={false}
                          strokeColor={spiderSuccess.color}
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
                          {formatCount(spiderStats?.totalItemsScraped)}
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
                          {spiderStats ? `${spiderStats.avgLatencyMs.toFixed(0)} ms` : NO_DATA}
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
    </PageContainer>
  )
})

export default Dashboard
