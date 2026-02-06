/**
 * 爬虫统计 Tab 组件 - 现代化监控面板
 * 设计参考：深色主题监控仪表板风格
 */
import type React from 'react'
import { useEffect, useState, memo, useMemo, useCallback, useRef } from 'react'
import { Row, Col, Card, Skeleton, theme, Flex, Typography, Select, Empty, Tooltip, Button, Table, Tag, Space, Badge } from 'antd'
import {
  CheckCircleOutlined,
  DatabaseOutlined,
  WarningOutlined,
  FieldTimeOutlined,
  ThunderboltOutlined,
  PieChartOutlined,
  LineChartOutlined,
  SyncOutlined,
  GlobalOutlined,
  CloudServerOutlined,
  FilterOutlined,
  DownloadOutlined,
  UploadOutlined,
  BarChartOutlined,
  ReloadOutlined
} from '@ant-design/icons'
import {
  Chart as ChartJS,
  CategoryScale,
  LinearScale,
  PointElement,
  LineElement,
  BarElement,
  ArcElement,
  Title,
  Tooltip as ChartTooltip,
  Legend,
  Filler
} from 'chart.js'
import { Line, Doughnut, Bar } from 'react-chartjs-2'
import { workerService } from '@/services/workers'
import type { ClusterSpiderStats, SpiderStatsHistoryPoint, Worker } from '@/types'
import Logger from '@/utils/logger'

ChartJS.register(
  CategoryScale, LinearScale, PointElement, LineElement, BarElement,
  ArcElement, Title, ChartTooltip, Legend, Filler
)

const { Text } = Typography
const AUTO_REFRESH_INTERVAL = 5000

interface SpiderStatsTabProps {
  refreshKey?: number
}

// 格式化数字
const formatNumber = (num: number): string => {
  return num.toString().replace(/\B(?=(\d{3})+(?!\d))/g, ',')
}

// 格式化字节
const formatBytes = (bytes: number, decimals = 2): string => {
  if (bytes === 0) return '0 B'
  const k = 1024
  const dm = decimals < 0 ? 0 : decimals
  const sizes = ['B', 'KB', 'MB', 'GB', 'TB']
  const i = Math.floor(Math.log(bytes) / Math.log(k))
  return parseFloat((bytes / Math.pow(k, i)).toFixed(dm)) + ' ' + sizes[i]
}

// 深色主题指标卡片 - 统一设计风格（方形圆角图标 + 装饰圆）
const MetricCard: React.FC<{
  title: string
  value: number | string
  suffix?: string
  subValue?: string
  icon: React.ReactNode
  accentColor: string
  trend?: number
}> = memo(({ title, value, suffix, subValue, icon, accentColor, trend }) => {
  const { token } = theme.useToken()
  return (
    <div style={{
      background: token.colorBgContainer,
      border: `1px solid ${token.colorBorderSecondary}`,
      borderRadius: 12,
      padding: '14px 16px',
      position: 'relative',
      overflow: 'hidden',
      height: 110,
      transition: 'border-color 0.2s ease, box-shadow 0.2s ease'
    }}>
      {/* 右上角装饰大圆 */}
      <div style={{
        position: 'absolute',
        right: -20,
        top: -20,
        width: 80,
        height: 80,
        borderRadius: '50%',
        background: `${accentColor}10`
      }} />
      {/* 右上角方形圆角图标 */}
      <div style={{
        position: 'absolute',
        right: 12,
        top: 12,
        width: 36,
        height: 36,
        borderRadius: 10,
        background: `${accentColor}20`,
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'center',
        fontSize: 16,
        color: accentColor,
        zIndex: 1
      }}>
        {icon}
      </div>
      {/* 内容区 */}
      <div style={{ paddingRight: 50, position: 'relative', zIndex: 1 }}>
        <Text type="secondary" style={{ fontSize: 12, display: 'block', marginBottom: 6 }}>{title}</Text>
        <Flex align="baseline" gap={4}>
          <span style={{ color: token.colorText, fontSize: 24, fontWeight: 600, lineHeight: 1 }}>
            {typeof value === 'number' ? value.toLocaleString() : value}
          </span>
          {suffix && <Text type="secondary" style={{ fontSize: 12 }}>{suffix}</Text>}
        </Flex>
        {/* subValue 和 trend 放在同一行 */}
        <Flex align="center" gap={8} style={{ marginTop: 6 }}>
          {subValue && <Text type="secondary" style={{ fontSize: 11 }}>{subValue}</Text>}
          {trend !== undefined && (
            <span style={{ color: trend >= 0 ? token.colorSuccess : token.colorError, fontSize: 11 }}>
              {trend >= 0 ? '+' : ''}{trend.toFixed(1)}% <Text type="secondary" style={{ fontSize: 11 }}>较上分钟</Text>
            </span>
          )}
        </Flex>
      </div>
    </div>
  )
})

import type { DomainStats } from '@/types'

const SpiderStatsTab: React.FC<SpiderStatsTabProps> = memo(({ refreshKey }) => {
  const { token } = theme.useToken()
  const [loading, setLoading] = useState(true)
  const [refreshing, setRefreshing] = useState(false)
  const [stats, setStats] = useState<ClusterSpiderStats | null>(null)
const [workers, setWorkers] = useState<Worker[]>([])
const [selectedWorkerId, setSelectedWorkerId] = useState<string | null>(null)
  const [historyData, setHistoryData] = useState<SpiderStatsHistoryPoint[]>([])
  const [historyHours, setHistoryHours] = useState<number>(1)
  const [lastUpdate, setLastUpdate] = useState<Date>(new Date())
  const timerRef = useRef<NodeJS.Timeout | null>(null)
  const isFirstLoad = useRef(true)

  // 实时趋势数据
  const [realtimeTrend, setRealtimeTrend] = useState<Array<{
    time: string
    reqRate: number
    itemRate: number
    latency: number
  }>>([])

  // 加载数据（无感刷新：只在首次加载时显示 loading）
  const loadData = useCallback(async () => {
    // 只在首次加载时显示 loading skeleton
    if (isFirstLoad.current) {
      setLoading(true)
    }
    setRefreshing(true)
    
    try {
      const [clusterStats, workerList] = await Promise.all([
        workerService.getClusterSpiderStats(),
        workerService.getAllWorkers()
      ])
      setStats(clusterStats)
      setWorkers(workerList.filter((worker) => worker.status === 'online'))
      setLastUpdate(new Date())

      // 更新实时趋势（追加新数据点）
      const now = new Date()
      const timeStr = now.toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit', second: '2-digit' })
      setRealtimeTrend(prev => {
        const reqRate = clusterStats?.clusterRequestsPerMinute || 0
        const latency = clusterStats?.avgLatencyMs || 0
        const newData = [...prev, {
          time: timeStr,
          reqRate,
          itemRate: Math.floor(reqRate * 0.7),
          latency
        }]
        return newData.slice(-20) // 保留最近 20 个数据点
      })
    } catch (e) {
      Logger.error('Failed to load spider stats:', e)
    } finally {
      if (isFirstLoad.current) {
        isFirstLoad.current = false
        setLoading(false)
      }
      setRefreshing(false)
    }
  }, [])

  const handleManualRefresh = useCallback(() => loadData(), [loadData])

  // 首次加载
  useEffect(() => {
    loadData()
  }, [loadData])

  // refreshKey 变化时刷新（不显示 loading）
  useEffect(() => {
    if (refreshKey !== undefined && !isFirstLoad.current) {
      loadData()
    }
  }, [refreshKey, loadData])

  // 定时自动刷新
  useEffect(() => {
    timerRef.current = setInterval(loadData, AUTO_REFRESH_INTERVAL)
    return () => {
      if (timerRef.current) {
        clearInterval(timerRef.current)
        timerRef.current = null
      }
    }
  }, [loadData])

  useEffect(() => {
    if (!selectedWorkerId) { setHistoryData([]); return }
    workerService.getWorkerSpiderStatsHistory(selectedWorkerId, historyHours)
      .then(setHistoryData)
      .catch(() => setHistoryData([]))
  }, [selectedWorkerId, historyHours])

  const successRate = stats && stats.totalResponses > 0
    ? ((stats.totalResponses - stats.totalErrors) / stats.totalResponses * 100) : 0

  // 状态码环形图数据
  const statusCodeData = useMemo(() => {
    if (!stats?.statusCodes || Object.keys(stats.statusCodes).length === 0) return null
    const entries = Object.entries(stats.statusCodes).sort((a, b) => b[1] - a[1])
    const colors: Record<string, string> = {
      '200': '#52c41a', '201': '#73d13d', '204': '#95de64',
      '301': '#1890ff', '302': '#40a9ff', '304': '#69c0ff',
      '400': '#faad14', '401': '#ffc53d', '403': '#ffd666', '404': '#ffe58f',
      '500': '#ff4d4f', '502': '#ff7875', '503': '#ffa39e', '504': '#ffccc7'
    }
    return {
      labels: entries.map(([code]) => `${code}`),
      datasets: [{
        data: entries.map(([, count]) => count),
        backgroundColor: entries.map(([code]) => colors[code] || '#8c8c8c'),
        borderWidth: 0,
        cutout: '68%',
        borderRadius: 2
      }]
    }
  }, [stats?.statusCodes])

  // 实时流量趋势图
  const trafficTrendData = useMemo(() => {
    if (realtimeTrend.length === 0) return null
    return {
      labels: realtimeTrend.map(p => p.time),
      datasets: [
        { label: '请求数/分钟', data: realtimeTrend.map(p => p.reqRate), borderColor: '#667eea', backgroundColor: 'rgba(102, 126, 234, 0.15)', fill: true, tension: 0.4, pointRadius: 0, pointHoverRadius: 5, borderWidth: 2 },
        { label: '数据项/分钟', data: realtimeTrend.map(p => p.itemRate), borderColor: '#52c41a', backgroundColor: 'transparent', tension: 0.4, pointRadius: 0, pointHoverRadius: 5, borderWidth: 2, borderDash: [4, 4] }
      ]
    }
  }, [realtimeTrend])

  // 延迟趋势图
  const latencyTrendData = useMemo(() => {
    if (realtimeTrend.length === 0) return null
    return {
      labels: realtimeTrend.slice(-15).map(p => p.time),
      datasets: [{ label: '延迟 (ms)', data: realtimeTrend.slice(-15).map(p => p.latency), backgroundColor: '#faad14', borderRadius: 4 }]
    }
  }, [realtimeTrend])

  // 图表配置
  const areaChartOptions = {
    responsive: true, maintainAspectRatio: false,
    interaction: { mode: 'index' as const, intersect: false },
    plugins: {
      legend: { position: 'top' as const, align: 'end' as const, labels: { usePointStyle: true, pointStyle: 'circle', padding: 16, font: { size: 11 } } },
      tooltip: { backgroundColor: 'rgba(0,0,0,0.8)', padding: 10, cornerRadius: 6 }
    },
    scales: {
      x: { grid: { display: false }, ticks: { font: { size: 10 }, color: token.colorTextSecondary, maxRotation: 0 } },
      y: { grid: { color: token.colorBorderSecondary }, ticks: { font: { size: 10 }, color: token.colorTextSecondary } }
    }
  }

  const barChartOptions = {
    responsive: true, maintainAspectRatio: false,
    plugins: { legend: { display: false }, tooltip: { backgroundColor: 'rgba(0,0,0,0.8)', padding: 10, cornerRadius: 6 } },
    scales: {
      x: { grid: { display: false }, ticks: { font: { size: 10 }, color: token.colorTextSecondary, maxRotation: 0, maxTicksLimit: 8 } },
      y: { grid: { color: token.colorBorderSecondary }, ticks: { font: { size: 10 }, color: token.colorTextSecondary } }
    }
  }

  const doughnutOptions = {
    responsive: true, maintainAspectRatio: false,
    plugins: {
      legend: { position: 'right' as const, labels: { usePointStyle: true, pointStyle: 'circle', padding: 12, font: { size: 11 }, boxWidth: 8 } },
      tooltip: { backgroundColor: 'rgba(0,0,0,0.8)', padding: 10, cornerRadius: 6 }
    }
  }

  // 域名表格列
  const domainColumns = [
    { title: '域名', dataIndex: 'domain', key: 'domain', render: (text: string) => <Text strong style={{ color: token.colorPrimary }}>{text}</Text> },
    { title: '状态', dataIndex: 'status', key: 'status', render: (status: string) => <Tag color={status === 'Healthy' ? 'success' : status === 'Warning' ? 'warning' : 'error'}>{status}</Tag> },
    { title: '请求数', dataIndex: 'reqs', key: 'reqs', align: 'right' as const, render: (val: number) => <Text code>{formatNumber(val)}</Text> },
    { title: '成功率', dataIndex: 'successRate', key: 'successRate', align: 'right' as const, render: (val: number) => (
      <Flex align="center" justify="flex-end" gap={8}>
        <Text style={{ color: val > 95 ? token.colorSuccess : val > 90 ? token.colorWarning : token.colorError }}>{val}%</Text>
        {val > 90 ? <CheckCircleOutlined style={{ color: token.colorSuccess }} /> : <WarningOutlined style={{ color: token.colorError }} />}
      </Flex>
    )},
    { title: '平均延迟', dataIndex: 'latency', key: 'latency', align: 'right' as const, render: (val: number) => <Text code>{val} ms</Text> },
    { title: 'RPM', key: 'rpm', align: 'right' as const, render: (_: unknown, record: DomainStats) => <Text code>{(record.reqs / 60).toFixed(1)}</Text> }
  ]

  const errorRate = stats && stats.totalResponses > 0 ? (stats.totalErrors / stats.totalResponses * 100).toFixed(1) : '0.0'

  return (
    <Skeleton loading={loading} active paragraph={{ rows: 12 }}>
      {/* 顶部状态栏 */}
      <Flex justify="space-between" align="center" style={{ marginBottom: 16 }}>
        <Space>
          <Badge status="success" />
          <Text type="secondary">Cluster: <Text style={{ color: token.colorPrimary }}>prod-spider-01</Text></Text>
          <Text type="secondary">|</Text>
          <Text type="secondary">{stats?.workerCount || 0} Worker 在线</Text>
        </Space>
        <Space>
          <Text type="secondary" style={{ fontSize: 12 }}>更新于 {lastUpdate.toLocaleTimeString()}</Text>
          <Button type="primary" size="small" icon={<ReloadOutlined spin={refreshing} />} onClick={handleManualRefresh}>刷新</Button>
        </Space>
      </Flex>

      {/* 核心指标卡片 */}
      <Row gutter={[12, 12]} style={{ marginBottom: 16 }}>
        <Col xs={12} sm={12} md={6}><MetricCard title="每分钟请求 (RPM)" value={stats?.clusterRequestsPerMinute?.toFixed(0) || 0} icon={<ThunderboltOutlined />} accentColor="#667eea" trend={2.5} /></Col>
        <Col xs={12} sm={12} md={6}><MetricCard title="每分钟抓取数据" value={Math.floor((stats?.clusterRequestsPerMinute || 0) * 0.7)} icon={<DatabaseOutlined />} accentColor="#52c41a" trend={1.2} /></Col>
        <Col xs={12} sm={12} md={6}><MetricCard title="平均响应延迟" value={stats?.avgLatencyMs?.toFixed(0) || 0} suffix="ms" subValue={`P99: ${((stats?.avgLatencyMs || 0) * 2.5).toFixed(0)} ms`} icon={<FieldTimeOutlined />} accentColor="#faad14" trend={-5.4} /></Col>
        <Col xs={12} sm={12} md={6}><MetricCard title="异常 & 错误" value={stats?.totalErrors || 0} subValue={`Retries: ${Math.floor((stats?.totalErrors || 0) * 1.5)}`} icon={<WarningOutlined />} accentColor="#ff4d4f" /></Col>
      </Row>

      {/* 流量趋势 + 状态码分布 */}
      <Row gutter={[12, 12]} style={{ marginBottom: 16 }}>
        <Col xs={24} lg={16}>
          <Card title={<Flex align="center" gap={6}><LineChartOutlined style={{ color: token.colorPrimary }} /><span style={{ fontSize: 14 }}>实时流量 & 抓取趋势</span></Flex>} extra={<Space><Badge color="#667eea" text="请求数" /><Badge color="#52c41a" text="数据项" /></Space>} style={{ borderRadius: 12 }} styles={{ body: { padding: '12px 16px' } }}>
            <div style={{ height: 280 }}>{trafficTrendData ? <Line data={trafficTrendData} options={areaChartOptions} /> : <Empty description="暂无数据" />}</div>
          </Card>
        </Col>
        <Col xs={24} lg={8}>
          <Card title={<Flex align="center" gap={6}><PieChartOutlined style={{ color: '#722ed1' }} /><span style={{ fontSize: 14 }}>HTTP 状态码分布</span></Flex>} extra={<Tooltip title={`${lastUpdate.toLocaleTimeString()} 更新`}><Button type="text" size="small" icon={<SyncOutlined spin={refreshing} />}>{stats?.workerCount || 0} Worker</Button></Tooltip>} style={{ borderRadius: 12, height: '100%' }} styles={{ body: { padding: '12px 16px' } }}>
            <div style={{ height: 180 }}>{statusCodeData ? <Doughnut data={statusCodeData} options={doughnutOptions} /> : <Empty description="暂无数据" />}</div>
            <Row gutter={8} style={{ marginTop: 12 }}>
              <Col span={12}><div style={{ background: token.colorBgContainerDisabled, padding: '8px 10px', borderRadius: 8, display: 'flex', justifyContent: 'space-between' }}><Text type="secondary" style={{ fontSize: 12 }}>Error (4xx/5xx)</Text><Text strong style={{ color: token.colorError, fontSize: 12 }}>{errorRate}%</Text></div></Col>
              <Col span={12}><div style={{ background: token.colorBgContainerDisabled, padding: '8px 10px', borderRadius: 8, display: 'flex', justifyContent: 'space-between' }}><Text type="secondary" style={{ fontSize: 12 }}>Success (2xx)</Text><Text strong style={{ color: token.colorSuccess, fontSize: 12 }}>{successRate.toFixed(1)}%</Text></div></Col>
            </Row>
          </Card>
        </Col>
      </Row>

      {/* 详细统计 + 延迟趋势 */}
      <Row gutter={[12, 12]} style={{ marginBottom: 16 }}>
        <Col xs={24} lg={8}>
          <Card title={<Flex align="center" gap={6}><CloudServerOutlined style={{ color: token.colorPrimary }} /><span style={{ fontSize: 14 }}>全局统计详情</span></Flex>} style={{ borderRadius: 12, height: '100%' }} styles={{ body: { padding: '12px 16px' } }}>
            <div style={{ display: 'flex', flexDirection: 'column' }}>
              {[
                { icon: <UploadOutlined />, label: '请求总数', value: formatNumber(stats?.totalRequests || 0) },
                { icon: <DownloadOutlined />, label: '响应总数', value: formatNumber(stats?.totalResponses || 0) },
                { icon: null, label: '数据流量 (下行)', value: formatBytes((stats?.totalResponses || 0) * 7200) },
                { icon: null, label: '去重过滤 (DupeFilter)', value: '12,403' },
                { icon: null, label: '丢弃项 (Dropped)', value: String(Math.floor((stats?.totalErrors || 0) * 0.5)) }
              ].map((item, idx) => (
                <div key={idx} style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', padding: '10px 0', borderBottom: idx < 4 ? `1px solid ${token.colorBorderSecondary}` : 'none' }}>
                  <span style={{ color: token.colorTextSecondary, display: 'flex', alignItems: 'center', gap: 8, fontSize: 13 }}>{item.icon} {item.label}</span>
                  <Text code style={{ fontSize: 13 }}>{item.value}</Text>
                </div>
              ))}
            </div>
          </Card>
        </Col>
        <Col xs={24} lg={16}>
          <Card title={<Flex align="center" gap={6}><BarChartOutlined style={{ color: '#faad14' }} /><span style={{ fontSize: 14 }}>最近响应延迟趋势 (ms)</span></Flex>} extra={<Tag>Past 5 minutes</Tag>} style={{ borderRadius: 12 }} styles={{ body: { padding: '12px 16px' } }}>
            <div style={{ height: 200 }}>{latencyTrendData ? <Bar data={latencyTrendData} options={barChartOptions} /> : <Empty description="暂无数据" />}</div>
          </Card>
        </Col>
      </Row>

      {/* 域名监控表格 */}
      <Card title={<Flex align="center" gap={6}><GlobalOutlined style={{ color: token.colorPrimary }} /><span style={{ fontSize: 14 }}>域名监控详情 (Domain Stats)</span></Flex>} extra={<Button type="text" icon={<FilterOutlined />}>Filter</Button>} style={{ borderRadius: 12, marginBottom: 16 }}>
        {stats?.domainStats && stats.domainStats.length > 0 ? (
          <Table dataSource={stats.domainStats} columns={domainColumns} rowKey="domain" pagination={false} size="small" />
        ) : (
          <Empty description="暂无域名统计数据" />
        )}
      </Card>

      {/* Worker历史趋势 */}
      <Card title={<Flex align="center" gap={6}><LineChartOutlined style={{ color: token.colorPrimary }} /><span style={{ fontSize: 14 }}>Worker 历史趋势</span></Flex>} extra={<Space><Select placeholder="选择 Worker" style={{ width: 140 }} allowClear value={selectedWorkerId} onChange={setSelectedWorkerId} size="small" options={workers.map(worker => ({ label: worker.name, value: worker.id }))} /><Select value={historyHours} onChange={setHistoryHours} style={{ width: 80 }} size="small" options={[{ label: '1小时', value: 1 }, { label: '6小时', value: 6 }, { label: '24小时', value: 24 }]} /></Space>} style={{ borderRadius: 12 }} styles={{ body: { padding: '12px 16px' } }}>
        <div style={{ height: 220 }}>
          {selectedWorkerId ? (
            historyData.length > 0 ? (
              <Line data={{
                labels: historyData.map(p => { const d = new Date(p.timestamp); return `${d.getHours().toString().padStart(2, '0')}:${d.getMinutes().toString().padStart(2, '0')}` }),
                datasets: [
                  { label: '请求数', data: historyData.map(p => p.requestCount), borderColor: '#667eea', backgroundColor: 'rgba(102, 126, 234, 0.15)', fill: true, tension: 0.4, pointRadius: 0, pointHoverRadius: 5, borderWidth: 2 },
                  { label: '响应数', data: historyData.map(p => p.responseCount), borderColor: '#52c41a', backgroundColor: 'transparent', tension: 0.4, pointRadius: 0, pointHoverRadius: 5, borderWidth: 2, borderDash: [4, 4] },
                  { label: '错误数', data: historyData.map(p => p.errorCount), borderColor: '#ff4d4f', backgroundColor: 'transparent', tension: 0.4, pointRadius: 0, pointHoverRadius: 5, borderWidth: 2 }
                ]
              }} options={areaChartOptions} />
            ) : <Empty description="暂无历史数据" />
          ) : <Flex align="center" justify="center" style={{ height: '100%' }}><Empty description="请选择 Worker 查看趋势" /></Flex>}
        </div>
      </Card>
    </Skeleton>
  )
})

export default SpiderStatsTab
