import React, { useEffect, useState, memo } from 'react'
import { Row, Col, Card, Statistic, Progress, Alert, Button, Space, Tabs } from 'antd'
import {
  ProjectOutlined,
  PlayCircleOutlined,
  CheckCircleOutlined,
  UserOutlined,
  DatabaseOutlined,
  HddOutlined,
  ThunderboltOutlined,
  SyncOutlined,
  MonitorOutlined,
  ClockCircleOutlined,
  DashboardOutlined
} from '@ant-design/icons'

import { useAuth } from '@/hooks/useAuth'
import { PLATFORM_TITLE } from '@/config/app'
import { dashboardService, type DashboardStats, type SystemMetrics } from '@/services/dashboard'
// 懒加载监控页，避免在仪表盘初始加载时拉入 Chart.js 相关依赖
const MonitorTab = React.lazy(() => import('@/pages/Monitor'))
import './dashboard.css'

const Dashboard: React.FC = memo(() => {
  const { user } = useAuth()
  const [loading, setLoading] = useState(false)
  const [dashboardStats, setDashboardStats] = useState<DashboardStats | null>(null)
  const [systemMetrics, setSystemMetrics] = useState<SystemMetrics | null>(null)
  const [lastUpdated, setLastUpdated] = useState<Date | null>(null)
  const [activeTab, setActiveTab] = useState<string>('overview')

  // 加载仪表板数据
  const loadDashboardData = async () => {
    setLoading(true)
    try {
      const [stats, metrics] = await Promise.all([
        dashboardService.getDashboardStats(),
        dashboardService.getSystemMetrics()
      ])
      setDashboardStats(stats)
      setSystemMetrics(metrics)
      setLastUpdated(new Date())
    } catch (error) {
      console.error('Failed to load dashboard data:', error)
    } finally {
      setLoading(false)
    }
  }

  // 刷新系统指标
  const refreshMetrics = async () => {
    try {
      setLoading(true)
      const refreshedMetrics = await dashboardService.refreshSystemMetrics()
      setSystemMetrics(refreshedMetrics)
      setLastUpdated(new Date())
    } catch (error) {
      console.error('Failed to refresh metrics:', error)
    } finally {
      setLoading(false)
    }
  }

  // 初始化加载数据
  useEffect(() => {
    loadDashboardData()
  }, [])

  // 定时刷新数据（每30秒）
  useEffect(() => {
    const interval = setInterval(() => {
      loadDashboardData()
    }, 30000) // 30秒刷新一次

    return () => clearInterval(interval)
  }, [])

  // 格式化运行时间
  const formatUptime = (seconds: number): string => {
    const days = Math.floor(seconds / 86400)
    const hours = Math.floor((seconds % 86400) / 3600)
    const mins = Math.floor((seconds % 3600) / 60)
    
    if (days > 0) return `${days}天 ${hours}小时`
    if (hours > 0) return `${hours}小时 ${mins}分钟`
    return `${mins}分钟`
  }

  // 获取系统状态颜色
  const getSystemStatusColor = (status: string): string => {
    switch (status) {
      case 'normal': return '#52c41a'
      case 'warning': return '#faad14'
      case 'error': return '#ff4d4f'
      default: return '#d9d9d9'
    }
  }

  // 获取系统状态文本
  const getSystemStatusText = (status: string): string => {
    switch (status) {
      case 'normal': return '正常'
      case 'warning': return '警告'
      case 'error': return '异常'
      default: return '未知'
    }
  }

  return (
    <div style={{ padding: '24px' }}>
      {/* 页面标题 */}
      <div style={{ marginBottom: '24px' }}>
        <Space align="center" style={{ width: '100%', justifyContent: 'space-between' }}>
          <div>
            <h1 style={{ fontSize: '24px', fontWeight: 'bold', margin: 0 }}>
              🎉 欢迎使用 {PLATFORM_TITLE}
            </h1>
            <p style={{ margin: '8px 0 0 0', opacity: 0.65 }}>
              您好，{user?.username || 'admin'}！欢迎来到您的控制台
            </p>
          </div>
          <Space>
            {lastUpdated && activeTab === 'overview' && (
              <span style={{ opacity: 0.6, fontSize: '12px' }}>
                <ClockCircleOutlined /> 最后更新: {lastUpdated.toLocaleTimeString()}
              </span>
            )}
            {activeTab === 'overview' && (
              <Button 
                icon={<SyncOutlined spin={loading} />} 
                onClick={refreshMetrics}
                loading={loading}
                size="small"
              >
                刷新
              </Button>
            )}
          </Space>
        </Space>
      </div>

      {/* Tabs 切换 */}
      <Tabs
        destroyOnHidden
        activeKey={activeTab}
        onChange={setActiveTab}
        items={[
          {
            key: 'overview',
            label: (
              <span>
                <DashboardOutlined /> 概览
              </span>
            ),
            children: (
              <div>
                {/* 统计卡片 */}
                <Row gutter={[16, 16]} style={{ marginBottom: 24 }}>
                  <Col xs={24} sm={12} lg={6}>
                    <Card loading={loading}>
                      <Statistic
                        title="项目总数"
                        value={dashboardStats?.projects.total || 0}
                        prefix={<ProjectOutlined />}
                        valueStyle={{ color: '#1890ff' }}
                      />
                    </Card>
                  </Col>
                  <Col xs={24} sm={12} lg={6}>
                    <Card loading={loading}>
                      <Statistic
                        title="活跃任务"
                        value={dashboardStats?.tasks.active || 0}
                        prefix={<PlayCircleOutlined />}
                        valueStyle={{ color: '#52c41a' }}
                      />
                    </Card>
                  </Col>
                  <Col xs={24} sm={12} lg={6}>
                    <Card loading={loading}>
                      <Statistic
                        title="系统状态"
                        value={getSystemStatusText(dashboardStats?.system.status || 'unknown')}
                        prefix={<CheckCircleOutlined />}
                        valueStyle={{ color: getSystemStatusColor(dashboardStats?.system.status || 'unknown') }}
                      />
                    </Card>
                  </Col>
                  <Col xs={24} sm={12} lg={6}>
                    <Card loading={loading}>
                      <Statistic
                        title="系统运行时间"
                        value={dashboardStats?.system.uptime ? formatUptime(dashboardStats.system.uptime) : '未知'}
                        prefix={<ClockCircleOutlined />}
                        valueStyle={{ color: '#722ed1' }}
                      />
                    </Card>
                  </Col>
                </Row>

                {/* 任务监控 */}
                <Row gutter={[16, 16]} style={{ marginBottom: 24 }}>
                  <Col xs={24} lg={12}>
                    <Card title={<Space><MonitorOutlined />任务执行统计</Space>} loading={loading}>
                      <Row gutter={16}>
                        <Col span={12}>
                          <Statistic
                            title="正在运行"
                            value={dashboardStats?.tasks.running || 0}
                            valueStyle={{ color: '#1890ff' }}
                          />
                        </Col>
                        <Col span={12}>
                          <Statistic
                            title="总执行次数"
                            value={systemMetrics?.total_executions || 0}
                            valueStyle={{ color: '#52c41a' }}
                          />
                        </Col>
                      </Row>
                      <Row gutter={16} style={{ marginTop: '16px' }}>
                        <Col span={12}>
                          <Statistic
                            title="成功率"
                            value={systemMetrics?.success_rate || 0}
                            precision={1}
                            suffix="%"
                            valueStyle={{ color: systemMetrics?.success_rate && systemMetrics.success_rate > 80 ? '#52c41a' : '#faad14' }}
                          />
                        </Col>
                        <Col span={12}>
                          <Statistic
                            title="队列大小"
                            value={systemMetrics?.queue_size || 0}
                            valueStyle={{ color: '#722ed1' }}
                          />
                        </Col>
                      </Row>
                    </Card>
                  </Col>
                  <Col xs={24} lg={12}>
                    <Card title={<Space><ProjectOutlined />项目统计</Space>} loading={loading}>
                      <Row gutter={16}>
                        <Col span={8}>
                          <Statistic
                            title="活跃项目"
                            value={dashboardStats?.projects.active || 0}
                            valueStyle={{ color: '#52c41a' }}
                          />
                        </Col>
                        <Col span={8}>
                          <Statistic
                            title="已完成任务"
                            value={dashboardStats?.tasks.completed || 0}
                            valueStyle={{ color: '#1890ff' }}
                          />
                        </Col>
                        <Col span={8}>
                          <Statistic
                            title="失败任务"
                            value={dashboardStats?.tasks.failed || 0}
                            valueStyle={{ color: '#ff4d4f' }}
                          />
                        </Col>
                      </Row>
                    </Card>
                  </Col>
                </Row>

                {/* 系统状态警告 */}
                {dashboardStats?.system.status === 'warning' && (
                  <Alert
                    message="系统性能警告"
                    description="系统资源使用率较高，建议关注CPU、内存或磁盘使用情况。"
                    type="warning"
                    showIcon
                    style={{ marginBottom: 24 }}
                  />
                )}
                {dashboardStats?.system.status === 'error' && (
                  <Alert
                    message="系统状态异常"
                    description="系统资源使用率过高，可能影响服务稳定性，请及时处理。"
                    type="error"
                    showIcon
                    style={{ marginBottom: 24 }}
                  />
                )}

                {/* 功能介绍 */}
                <Row gutter={[16, 16]} style={{ marginBottom: 24 }}>
                  <Col xs={24} lg={12}>
                    <Card title="📋 平台功能" variant="borderless">
                      <div style={{ lineHeight: '2' }}>
                        <p><strong>🎯 项目管理：</strong>创建和管理您的代码项目，支持多种项目类型</p>
                        <p><strong>⚡ 任务调度：</strong>灵活的任务调度系统，支持定时和手动执行</p>
                        <p><strong>📊 实时监控：</strong>实时查看任务执行状态和日志输出</p>
                        <p><strong>🔌 API接口：</strong>完整的RESTful API，支持第三方集成</p>
                      </div>
                    </Card>
                  </Col>

                  <Col xs={24} lg={12}>
                    <Card title="ℹ️ 系统信息" variant="borderless">
                      <div style={{ lineHeight: '2' }}>
                        <p><strong>版本：</strong>v1.3.0</p>
                        <p><strong>当前用户：</strong>{user?.username || 'admin'}</p>
                        <p><strong>登录状态：</strong>✅ 已登录</p>
                        <p><strong>权限级别：</strong>管理员</p>
                        <p><strong>后端状态：</strong>{dashboardStats?.system.status === 'normal' ? '✅ 运行正常' : dashboardStats?.system.status === 'warning' ? '⚠️ 运行警告' : '❌ 运行异常'}</p>
                        <p><strong>前端状态：</strong>✅ 运行正常</p>
                        {systemMetrics && (
                          <p><strong>活跃任务数：</strong>{systemMetrics.active_tasks} 个</p>
                        )}
                      </div>
                    </Card>
                  </Col>
                </Row>
              </div>
            ),
          },
          {
            key: 'monitor',
            label: (
              <span>
                <MonitorOutlined /> 监控中心
              </span>
            ),
            children: (
              <div>
                {/* 本机资源监控卡片 */}
                <React.Suspense fallback={<div style={{ padding: 24 }}>加载监控中心...</div>}>
                <Row gutter={[16, 16]} style={{ marginBottom: 24 }}>
                  <Col xs={24} lg={8}>
                    <Card title={<Space><DatabaseOutlined />内存使用情况</Space>} loading={loading}>
                      {systemMetrics?.memory_usage ? (
                        <div>
                          <div style={{ display: 'flex', alignItems: 'center', gap: '12px' }}>
                            <Progress
                              percent={Math.round(systemMetrics.memory_usage.percent)}
                              status={systemMetrics.memory_usage.percent > 80 ? 'exception' : 'normal'}
                              strokeColor={systemMetrics.memory_usage.percent > 80 ? '#ff4d4f' : '#1890ff'}
                              trailColor="rgba(0, 0, 0, 0.06)"
                              strokeWidth={10}
                              showInfo={false}
                              style={{ flex: 1 }}
                            />
                            <span style={{ fontSize: '14px', minWidth: '40px', textAlign: 'right' }}>
                              {Math.round(systemMetrics.memory_usage.percent)}%
                            </span>
                          </div>
                          <div style={{ marginTop: '12px', fontSize: '12px', opacity: 0.8 }}>
                            已用: {(systemMetrics.memory_usage.used / (1024**3)).toFixed(1)}GB / 
                            总计: {(systemMetrics.memory_usage.total / (1024**3)).toFixed(1)}GB
                          </div>
                        </div>
                      ) : (
                        <div style={{ textAlign: 'center', padding: '20px', opacity: 0.5 }}>
                          暂无数据
                        </div>
                      )}
                    </Card>
                  </Col>
                  <Col xs={24} lg={8}>
                    <Card title={<Space><ThunderboltOutlined />CPU使用情况</Space>} loading={loading}>
                      {systemMetrics?.cpu_usage ? (
                        <div>
                          <div style={{ display: 'flex', alignItems: 'center', gap: '12px' }}>
                            <Progress
                              percent={Math.round(systemMetrics.cpu_usage.percent)}
                              status={systemMetrics.cpu_usage.percent > 80 ? 'exception' : 'normal'}
                              strokeColor={systemMetrics.cpu_usage.percent > 80 ? '#ff4d4f' : '#52c41a'}
                              trailColor="rgba(0, 0, 0, 0.06)"
                              strokeWidth={10}
                              showInfo={false}
                              style={{ flex: 1 }}
                            />
                            <span style={{ fontSize: '14px', minWidth: '40px', textAlign: 'right' }}>
                              {Math.round(systemMetrics.cpu_usage.percent)}%
                            </span>
                          </div>
                          <div style={{ marginTop: '12px', fontSize: '12px', opacity: 0.8 }}>
                            核心数: {systemMetrics.cpu_usage.cores} 个
                          </div>
                        </div>
                      ) : (
                        <div style={{ textAlign: 'center', padding: '20px', opacity: 0.5 }}>
                          暂无数据
                        </div>
                      )}
                    </Card>
                  </Col>
                  <Col xs={24} lg={8}>
                    <Card title={<Space><HddOutlined />磁盘使用情况</Space>} loading={loading}>
                      {systemMetrics?.disk_usage ? (
                        <div>
                          <div style={{ display: 'flex', alignItems: 'center', gap: '12px' }}>
                            <Progress
                              percent={Math.round(systemMetrics.disk_usage.percent)}
                              status={systemMetrics.disk_usage.percent > 90 ? 'exception' : 'normal'}
                              strokeColor={systemMetrics.disk_usage.percent > 90 ? '#ff4d4f' : '#722ed1'}
                              trailColor="rgba(0, 0, 0, 0.06)"
                              strokeWidth={10}
                              showInfo={false}
                              style={{ flex: 1 }}
                            />
                            <span style={{ fontSize: '14px', minWidth: '40px', textAlign: 'right' }}>
                              {Math.round(systemMetrics.disk_usage.percent)}%
                            </span>
                          </div>
                          <div style={{ marginTop: '12px', fontSize: '12px', opacity: 0.8 }}>
                            已用: {(systemMetrics.disk_usage.used / (1024**3)).toFixed(1)}GB / 
                            总计: {(systemMetrics.disk_usage.total / (1024**3)).toFixed(1)}GB
                          </div>
                        </div>
                      ) : (
                        <div style={{ textAlign: 'center', padding: '20px', opacity: 0.5 }}>
                          暂无数据
                        </div>
                      )}
                    </Card>
                  </Col>
                </Row>

                {/* 节点监控 */}
                <MonitorTab />
                </React.Suspense>
              </div>
            ),
          },
        ]}
      />
    </div>
  )
})

export default Dashboard
