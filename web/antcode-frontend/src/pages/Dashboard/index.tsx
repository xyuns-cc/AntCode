import React, { useEffect, useState, memo } from 'react'
import { Row, Col, Card, Statistic, Progress, Alert, Button, Tabs, Flex, Typography, Skeleton, theme, Space } from 'antd'
import {
  ProjectOutlined, PlayCircleOutlined, CheckCircleOutlined, DatabaseOutlined, HddOutlined,
  ThunderboltOutlined, SyncOutlined, ClockCircleOutlined, DashboardOutlined,
  RocketOutlined, FieldTimeOutlined, BarChartOutlined, FileTextOutlined, AimOutlined,
  ApiOutlined, InfoCircleOutlined, WarningOutlined, CloseCircleOutlined
} from '@ant-design/icons'
import { useAuth } from '@/hooks/useAuth'
import { PLATFORM_TITLE } from '@/config/app'
import { dashboardService, type DashboardStats, type SystemMetrics } from '@/services/dashboard'

const { Title, Text } = Typography

interface StatCardProps {
  title: string; value: number | string; icon: React.ReactNode; color: string; loading?: boolean; suffix?: string; precision?: number
}

const StatCard: React.FC<StatCardProps> = memo(({ title, value, icon, color, loading, suffix, precision }) => (
  <Card hoverable styles={{ body: { padding: 20 } }} style={{ borderRadius: 12, transition: 'all 0.3s cubic-bezier(0.4, 0, 0.2, 1)' }}>
    <Skeleton loading={loading} active paragraph={false}>
      <Flex align="flex-start" justify="space-between">
        <div>
          <Text type="secondary" style={{ fontSize: 13, marginBottom: 8, display: 'block' }}>{title}</Text>
          <Statistic value={value} valueStyle={{ color, fontSize: 28, fontWeight: 600, lineHeight: 1.2 }} suffix={suffix} precision={precision} />
        </div>
        <Flex align="center" justify="center" style={{ width: 48, height: 48, borderRadius: 12, background: `${color}15`, color, fontSize: 22 }}>
          {icon}
        </Flex>
      </Flex>
    </Skeleton>
  </Card>
))

interface ResourceCardProps {
  title: string; icon: React.ReactNode; percent: number; used?: string; total?: string; loading?: boolean; color: string
}

const ResourceCard: React.FC<ResourceCardProps> = memo(({ title, icon, percent, used, total, loading, color }) => {
  const { token } = theme.useToken()
  return (
    <Card hoverable styles={{ body: { padding: 16 } }} style={{ borderRadius: 8, height: '100%' }}>
      <Skeleton loading={loading} active paragraph={{ rows: 2 }}>
        <Flex align="center" gap={8} style={{ marginBottom: 12 }}>
          <span style={{ fontSize: 16, color }}>{icon}</span>
          <Text strong style={{ fontSize: 14 }}>{title}</Text>
        </Flex>
        <Flex align="center" gap={12} style={{ marginBottom: 6 }}>
          <Progress percent={percent} status={percent > 80 ? 'exception' : 'normal'} strokeColor={percent > 80 ? token.colorError : color} trailColor={token.colorFillSecondary} showInfo={false} style={{ flex: 1 }} strokeWidth={6} />
          <Text strong style={{ minWidth: 38, textAlign: 'right', fontSize: 15 }}>{percent}%</Text>
        </Flex>
        {used && total && <Text type="secondary" style={{ fontSize: 11, display: 'block', lineHeight: 1.5 }}>已用: {used} / 总计: {total}</Text>}
      </Skeleton>
    </Card>
  )
})

const FeatureCard: React.FC<{ icon: React.ReactNode; title: string; description: string }> = memo(({ icon, title, description }) => {
  const { token } = theme.useToken()
  return (
    <Flex gap={12} style={{ padding: '12px 0' }}>
      <Flex align="center" justify="center" style={{ width: 36, height: 36, borderRadius: 8, background: token.colorPrimaryBg, color: token.colorPrimary, fontSize: 18, flexShrink: 0 }}>
        {icon}
      </Flex>
      <div><Text strong>{title}</Text><br /><Text type="secondary" style={{ fontSize: 13 }}>{description}</Text></div>
    </Flex>
  )
})

const Dashboard: React.FC = memo(() => {
  const { user } = useAuth()
  const { token } = theme.useToken()
  const [loading, setLoading] = useState(false)
  const [dashboardStats, setDashboardStats] = useState<DashboardStats | null>(null)
  const [systemMetrics, setSystemMetrics] = useState<SystemMetrics | null>(null)
  const [lastUpdated, setLastUpdated] = useState<Date | null>(null)
  const [activeTab, setActiveTab] = useState('overview')

  const normalizePercent = (value: unknown): number => {
    const n = Number(value)
    return Number.isFinite(n) ? Math.min(100, Math.max(0, Math.round(n))) : 0
  }

  const cpuPercent = normalizePercent(systemMetrics?.cpu_usage?.percent)
  const memoryPercent = normalizePercent(systemMetrics?.memory_usage?.percent)
  const diskPercent = normalizePercent(systemMetrics?.disk_usage?.percent)

  const loadDashboardData = async () => {
    setLoading(true)
    try {
      const [stats, metrics] = await Promise.all([dashboardService.getDashboardStats(), dashboardService.getSystemMetrics()])
      setDashboardStats(stats)
      setSystemMetrics(metrics)
      setLastUpdated(new Date())
    } catch (e) { console.error('Failed to load dashboard:', e) }
    finally { setLoading(false) }
  }

  const refreshMetrics = async () => {
    setLoading(true)
    try {
      setSystemMetrics(await dashboardService.refreshSystemMetrics())
      setLastUpdated(new Date())
    } catch (e) { console.error('Failed to refresh:', e) }
    finally { setLoading(false) }
  }

  useEffect(() => { loadDashboardData() }, [])
  useEffect(() => { const id = setInterval(loadDashboardData, 30000); return () => clearInterval(id) }, [])

  const formatUptime = (s: number) => {
    const d = Math.floor(s / 86400), h = Math.floor((s % 86400) / 3600), m = Math.floor((s % 3600) / 60)
    return d > 0 ? `${d}天 ${h}小时` : h > 0 ? `${h}小时 ${m}分钟` : `${m}分钟`
  }

  const getStatusColor = (status: string) => ({ normal: token.colorSuccess, warning: token.colorWarning, error: token.colorError }[status] || token.colorTextDisabled)
  const getStatusText = (status: string) => ({ normal: '正常', warning: '警告', error: '异常' }[status] || '未知')
  const formatBytes = (b: number) => `${(b / (1024 ** 3)).toFixed(1)}GB`

  return (
    <div>
      <Flex justify="space-between" align="flex-start" wrap="wrap" gap={16} style={{ marginBottom: 24 }}>
        <div>
          <Title level={3} style={{ margin: 0, fontWeight: 600 }}><RocketOutlined style={{ marginRight: 12, color: token.colorPrimary }} />欢迎使用 {PLATFORM_TITLE}</Title>
          <Text type="secondary" style={{ marginTop: 4, display: 'block' }}>您好，{user?.username || 'admin'}！这是您的控制台概览</Text>
        </div>
        <Space>
          {lastUpdated && activeTab === 'overview' && <Text type="secondary" style={{ fontSize: 12 }}><ClockCircleOutlined style={{ marginRight: 4 }} />更新于 {lastUpdated.toLocaleTimeString()}</Text>}
          {activeTab === 'overview' && <Button icon={<SyncOutlined spin={loading} />} onClick={refreshMetrics} loading={loading}>刷新数据</Button>}
        </Space>
      </Flex>

      <Tabs destroyInactiveTabPane activeKey={activeTab} onChange={setActiveTab} items={[
        {
          key: 'overview',
          label: <Flex align="center" gap={6}><DashboardOutlined /><span>概览</span></Flex>,
          children: (
            <>
              <Row gutter={[16, 16]} style={{ marginBottom: 24 }}>
                <Col xs={24} sm={12} lg={6}><StatCard title="项目总数" value={dashboardStats?.projects.total || 0} icon={<ProjectOutlined />} color={token.colorInfo} loading={loading} /></Col>
                <Col xs={24} sm={12} lg={6}><StatCard title="活跃任务" value={dashboardStats?.tasks.active || 0} icon={<PlayCircleOutlined />} color={token.colorSuccess} loading={loading} /></Col>
                <Col xs={24} sm={12} lg={6}><StatCard title="系统状态" value={getStatusText(dashboardStats?.system.status || '')} icon={<CheckCircleOutlined />} color={getStatusColor(dashboardStats?.system.status || '')} loading={loading} /></Col>
                <Col xs={24} sm={12} lg={6}><StatCard title="运行时间" value={dashboardStats?.system.uptime ? formatUptime(dashboardStats.system.uptime) : '未知'} icon={<FieldTimeOutlined />} color={token.purple} loading={loading} /></Col>
              </Row>

              <Row gutter={[16, 16]} style={{ marginBottom: 24 }}>
                <Col xs={24} lg={12}>
                  <Card title={<Flex align="center" gap={8}><BarChartOutlined style={{ color: token.colorPrimary }} /><span>任务执行统计</span></Flex>} style={{ borderRadius: 12 }}>
                    <Skeleton loading={loading} active>
                      <Row gutter={[24, 24]}>
                        <Col span={12}><Statistic title="正在运行" value={dashboardStats?.tasks.running || 0} valueStyle={{ color: token.colorInfo }} /></Col>
                        <Col span={12}><Statistic title="总执行次数" value={systemMetrics?.total_executions || 0} valueStyle={{ color: token.colorSuccess }} /></Col>
                        <Col span={12}><Statistic title="成功率" value={systemMetrics?.success_rate || 0} precision={1} suffix="%" valueStyle={{ color: (systemMetrics?.success_rate ?? 0) > 80 ? token.colorSuccess : token.colorWarning }} /></Col>
                        <Col span={12}><Statistic title="队列大小" value={systemMetrics?.queue_size || 0} valueStyle={{ color: token.purple }} /></Col>
                      </Row>
                    </Skeleton>
                  </Card>
                </Col>
                <Col xs={24} lg={12}>
                  <Card title={<Flex align="center" gap={8}><ProjectOutlined style={{ color: token.colorSuccess }} /><span>项目统计</span></Flex>} style={{ borderRadius: 12 }}>
                    <Skeleton loading={loading} active>
                      <Row gutter={[24, 24]}>
                        <Col span={8}><Statistic title="活跃项目" value={dashboardStats?.projects.active || 0} valueStyle={{ color: token.colorSuccess }} /></Col>
                        <Col span={8}><Statistic title="成功任务" value={dashboardStats?.tasks.success || 0} valueStyle={{ color: token.colorInfo }} /></Col>
                        <Col span={8}><Statistic title="失败任务" value={dashboardStats?.tasks.failed || 0} valueStyle={{ color: token.colorError }} /></Col>
                      </Row>
                    </Skeleton>
                  </Card>
                </Col>
              </Row>

              <Row gutter={[16, 16]} style={{ marginBottom: 24 }}>
                <Col xs={24} md={8}>
                  <ResourceCard
                    title="CPU 使用率"
                    icon={<ThunderboltOutlined />}
                    percent={cpuPercent}
                    loading={loading}
                    color={token.colorWarning}
                  />
                </Col>
                <Col xs={24} md={8}>
                  <ResourceCard
                    title="内存使用率"
                    icon={<DatabaseOutlined />}
                    percent={memoryPercent}
                    used={systemMetrics?.memory_usage ? formatBytes(systemMetrics.memory_usage.used) : undefined}
                    total={systemMetrics?.memory_usage ? formatBytes(systemMetrics.memory_usage.total) : undefined}
                    loading={loading}
                    color={token.colorSuccess}
                  />
                </Col>
                <Col xs={24} md={8}>
                  <ResourceCard
                    title="磁盘使用率"
                    icon={<HddOutlined />}
                    percent={diskPercent}
                    used={systemMetrics?.disk_usage ? formatBytes(systemMetrics.disk_usage.used) : undefined}
                    total={systemMetrics?.disk_usage ? formatBytes(systemMetrics.disk_usage.total) : undefined}
                    loading={loading}
                    color={token.colorInfo}
                  />
                </Col>
              </Row>

              {dashboardStats?.system.status === 'warning' && <Alert message="系统性能警告" description="系统资源使用率较高，建议关注CPU、内存或磁盘使用情况。" type="warning" showIcon style={{ marginBottom: 24, borderRadius: 12 }} />}
              {dashboardStats?.system.status === 'error' && <Alert message="系统状态异常" description="系统资源使用率过高，可能影响服务稳定性，请及时处理。" type="error" showIcon style={{ marginBottom: 24, borderRadius: 12 }} />}

              <Row gutter={[16, 16]}>
                <Col xs={24} lg={12}>
                  <Card title={<Flex align="center" gap={8}><FileTextOutlined style={{ color: token.colorPrimary }} /><span>平台功能</span></Flex>} variant="borderless" style={{ borderRadius: 12 }}>
                    <FeatureCard icon={<AimOutlined />} title="项目管理" description="创建和管理您的代码项目，支持多种项目类型" />
                    <FeatureCard icon={<ThunderboltOutlined />} title="任务调度" description="灵活的任务调度系统，支持定时和手动执行" />
                    <FeatureCard icon={<BarChartOutlined />} title="实时监控" description="实时查看任务执行状态和日志输出" />
                    <FeatureCard icon={<ApiOutlined />} title="API 接口" description="完整的 RESTful API，支持第三方集成" />
                  </Card>
                </Col>
                <Col xs={24} lg={12}>
                  <Card title={<Flex align="center" gap={8}><InfoCircleOutlined style={{ color: token.colorInfo }} /><span>系统信息</span></Flex>} variant="borderless" style={{ borderRadius: 12 }}>
                    <Flex vertical gap={8}>
                      <Flex justify="space-between"><Text type="secondary">版本</Text><Text strong>v1.3.0</Text></Flex>
                      <Flex justify="space-between"><Text type="secondary">当前用户</Text><Text strong>{user?.username || 'admin'}</Text></Flex>
                      <Flex justify="space-between"><Text type="secondary">登录状态</Text><Text type="success"><CheckCircleOutlined style={{ marginRight: 4 }} />已登录</Text></Flex>
                      <Flex justify="space-between"><Text type="secondary">权限级别</Text><Text strong>{user?.is_admin ? '管理员' : '普通用户'}</Text></Flex>
                      <Flex justify="space-between"><Text type="secondary">后端状态</Text><Text style={{ color: getStatusColor(dashboardStats?.system.status || '') }}>{dashboardStats?.system.status === 'normal' ? <><CheckCircleOutlined style={{ marginRight: 4 }} />运行正常</> : dashboardStats?.system.status === 'warning' ? <><WarningOutlined style={{ marginRight: 4 }} />运行警告</> : <><CloseCircleOutlined style={{ marginRight: 4 }} />运行异常</>}</Text></Flex>
                      {systemMetrics && <Flex justify="space-between"><Text type="secondary">活跃任务数</Text><Text strong>{systemMetrics.active_tasks} 个</Text></Flex>}
                    </Flex>
                  </Card>
                </Col>
              </Row>
            </>
          ),
        },
      ]} />
    </div>
  )
})

export default Dashboard
