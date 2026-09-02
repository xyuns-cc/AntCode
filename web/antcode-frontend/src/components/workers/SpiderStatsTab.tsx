/**
 * 爬虫统计 Tab 组件 - 现代化监控面板
 * 设计参考：深色主题监控仪表板风格
 */
import type React from 'react'
import { useEffect, useState, memo, useMemo, useCallback, useRef } from 'react'
import { Row, Col, Card, Skeleton, theme, Flex, Typography, Select, Empty, Tooltip, Button, Table, Tag, Space, Badge, Alert } from 'antd'
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
import MetricCard, { NO_METRIC } from './MetricCard'

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

const SpiderStatsTab: React.FC<SpiderStatsTabProps> = memo(({ refreshKey }) => {
  const { token } = theme.useToken()
  const [loading, setLoading] = useState(true)
  const [refreshing, setRefreshing] = useState(false)
  const [stats, setStats] = useState<ClusterSpiderStats | null>(null)
const [workers, setWorkers] = useState<Worker[]>([])
const [selectedWorkerId, setSelectedWorkerId] = useState<string | null>(null)
  const [historyData, setHistoryData] = useState<SpiderStatsHistoryPoint[]>([])
  const [historyError, setHistoryError] = useState<string | null>(null)
  const [historyHours, setHistoryHours] = useState<number>(1)
  const [lastUpdate, setLastUpdate] = useState<Date>(new Date())
  const timerRef = useRef<NodeJS.Timeout | null>(null)
  const isFirstLoad = useRef(true)

  // 轮询趋势只保留后端提供的完成任务请求窗口和平均延迟。
  const [realtimeTrend, setRealtimeTrend] = useState<Array<{
    time: string
    reqRate: number
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

      // 更新轮询趋势（追加新数据点）
      const now = new Date()
      const timeStr = now.toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit', second: '2-digit' })
      setRealtimeTrend(prev => {
        const reqRate = clusterStats?.clusterRequestsPerMinute || 0
        const latency = clusterStats?.avgLatencyMs || 0
        const newData = [...prev, {
          time: timeStr,
          reqRate,
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
    if (!selectedWorkerId) {
      setHistoryData([])
      setHistoryError(null)
      return
    }
    workerService.getWorkerSpiderStatsHistory(selectedWorkerId, historyHours)
      .then((data) => {
        setHistoryData(data)
        setHistoryError(null)
      })
      .catch((error: unknown) => {
        Logger.error('加载 Worker 爬虫历史指标失败:', error)
        setHistoryError(error instanceof Error ? error.message : '加载 Worker 爬虫历史指标失败')
      })
  }, [selectedWorkerId, historyHours])

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
        { label: '最近完成请求量', data: realtimeTrend.map(p => p.reqRate), borderColor: '#667eea', backgroundColor: 'rgba(102, 126, 234, 0.15)', fill: true, tension: 0.4, pointRadius: 0, pointHoverRadius: 5, borderWidth: 2 },
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
        <Text style={{ color: val >= 95 ? token.colorSuccess : val >= 90 ? token.colorWarning : token.colorError }}>{val}%</Text>
        {val >= 95 ? <CheckCircleOutlined style={{ color: token.colorSuccess }} /> : <WarningOutlined style={{ color: val >= 90 ? token.colorWarning : token.colorError }} />}
      </Flex>
    )},
    { title: '平均延迟', dataIndex: 'latency', key: 'latency', align: 'right' as const, render: (val: number) => <Text code>{val} ms</Text> }
  ]

  // 分母是响应数：这是状态码分布卡，2xx 占比只对收到的响应有定义，与「爬取成功率」（分母
  // 为请求总数，见 utils/spiderSuccessRate）是两件事。空集不是 0%，是没有样本可分布。
  const codeCounts = Object.entries(stats?.statusCodes ?? {})
  const countWithPrefix = (...prefixes: string[]) =>
    codeCounts.filter(([code]) => prefixes.some((p) => code.startsWith(p))).reduce((sum, [, n]) => sum + n, 0)
  const responseCount = codeCounts.reduce((sum, [, n]) => sum + n, 0)
  const httpShare = (part: number) => (responseCount ? `${(part / responseCount * 100).toFixed(1)}%` : '—')
  const httpSuccessShare = httpShare(countWithPrefix('2'))
  const httpErrorShare = httpShare(countWithPrefix('4', '5'))

  return (
    <Skeleton loading={loading} active paragraph={{ rows: 12 }}>
      {/* 顶部状态栏 */}
      <Flex justify="space-between" align="center" style={{ marginBottom: 16 }}>
        {/* 没拿到 stats 时不点绿灯：绿灯 + 「0 Worker 在线」读起来仍像一次正常读数。 */}
        <Space>
          <Badge status={stats ? 'success' : 'default'} />
          <Text type="secondary">{stats?.workerCount ?? NO_METRIC} Worker 在线</Text>
        </Space>
        <Space>
          <Text type="secondary" style={{ fontSize: 12 }}>更新于 {lastUpdate.toLocaleTimeString()}</Text>
          <Button type="primary" size="small" icon={<ReloadOutlined spin={refreshing} />} onClick={handleManualRefresh}>刷新</Button>
        </Space>
      </Flex>

      {/* 核心指标卡片只展示后端提供的真实数据；stats 为 null（这一轮没取到）时四张卡一律
          交给 MetricCard 渲染成占位，不再 `|| 0` 折算成「集群很闲」。 */}
      <Row gutter={[12, 12]} style={{ marginBottom: 16 }}>
        <Col xs={12} sm={12} md={6}><MetricCard title="最近完成请求量 (60秒)" value={stats?.clusterRequestsPerMinute?.toFixed(0)} icon={<ThunderboltOutlined />} accentColor="#667eea" /></Col>
        <Col xs={12} sm={12} md={6}><MetricCard title="抓取数据总数" value={stats?.totalItemsScraped} icon={<DatabaseOutlined />} accentColor="#52c41a" /></Col>
        <Col xs={12} sm={12} md={6}><MetricCard title="平均响应延迟" value={stats?.avgLatencyMs?.toFixed(0)} suffix="ms" icon={<FieldTimeOutlined />} accentColor="#faad14" /></Col>
        <Col xs={12} sm={12} md={6}><MetricCard title="异常 & 错误" value={stats?.totalErrors} icon={<WarningOutlined />} accentColor="#ff4d4f" /></Col>
      </Row>

      {/* 流量趋势 + 状态码分布 */}
      <Row gutter={[12, 12]} style={{ marginBottom: 16 }}>
        <Col xs={24} lg={16}>
          <Card title={<Flex align="center" gap={6}><LineChartOutlined style={{ color: token.colorPrimary }} /><span style={{ fontSize: 14 }}>最近完成请求量趋势</span></Flex>} extra={<Badge color="#667eea" text="60 秒窗口" />} style={{ borderRadius: 12 }} styles={{ body: { padding: '12px 16px' } }}>
            <div style={{ height: 280 }}>{trafficTrendData ? <Line data={trafficTrendData} options={areaChartOptions} /> : <Empty description="暂无数据" />}</div>
          </Card>
        </Col>
        <Col xs={24} lg={8}>
          <Card title={<Flex align="center" gap={6}><PieChartOutlined style={{ color: '#722ed1' }} /><span style={{ fontSize: 14 }}>HTTP 状态码分布</span></Flex>} extra={<Tooltip title={`${lastUpdate.toLocaleTimeString()} 更新`}><Button type="text" size="small" icon={<SyncOutlined spin={refreshing} />}>{stats?.workerCount ?? NO_METRIC} Worker</Button></Tooltip>} style={{ borderRadius: 12, height: '100%' }} styles={{ body: { padding: '12px 16px' } }}>
            <div style={{ height: 180 }}>{statusCodeData ? <Doughnut data={statusCodeData} options={doughnutOptions} /> : <Empty description="暂无数据" />}</div>
            <Row gutter={8} style={{ marginTop: 12 }}>
              <Col span={12}><div style={{ background: token.colorBgContainerDisabled, padding: '8px 10px', borderRadius: 8, display: 'flex', justifyContent: 'space-between' }}><Text type="secondary" style={{ fontSize: 12 }}>Error (4xx/5xx)</Text><Text strong style={{ color: token.colorError, fontSize: 12 }}>{httpErrorShare}</Text></div></Col>
              <Col span={12}><div style={{ background: token.colorBgContainerDisabled, padding: '8px 10px', borderRadius: 8, display: 'flex', justifyContent: 'space-between' }}><Text type="secondary" style={{ fontSize: 12 }}>Success (2xx)</Text><Text strong style={{ color: token.colorSuccess, fontSize: 12 }}>{httpSuccessShare}</Text></div></Col>
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
                // P1-round6 5.4: 移除合成运维数据 (响应*7200 假下行流量 /
                // 硬编码 DupeFilter=12,403 / 错误*0.5 假 Dropped)。真实无来源
                // 的字段改为 "—"; 后端后续如提供 total_bytes / dupe_filter /
                // dropped 字段再回填。
                { icon: null, label: '数据流量 (下行)', value: '—' },
                { icon: null, label: '去重过滤 (DupeFilter)', value: '—' },
                { icon: null, label: '丢弃项 (Dropped)', value: '—' }
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
          <Card title={<Flex align="center" gap={6}><BarChartOutlined style={{ color: '#faad14' }} /><span style={{ fontSize: 14 }}>最近响应延迟趋势 (ms)</span></Flex>} extra={<Tag>最近 20 个采样点</Tag>} style={{ borderRadius: 12 }} styles={{ body: { padding: '12px 16px' } }}>
            <div style={{ height: 200 }}>{latencyTrendData ? <Bar data={latencyTrendData} options={barChartOptions} /> : <Empty description="暂无数据" />}</div>
          </Card>
        </Col>
      </Row>

      {/* 域名监控表格 */}
      <Card title={<Flex align="center" gap={6}><GlobalOutlined style={{ color: token.colorPrimary }} /><span style={{ fontSize: 14 }}>域名监控详情 (Domain Stats)</span></Flex>} style={{ borderRadius: 12, marginBottom: 16 }}>
        {stats?.domainStats && stats.domainStats.length > 0 ? (
          <Table dataSource={stats.domainStats} columns={domainColumns} rowKey="domain" pagination={false} size="small" />
        ) : (
          <Empty description="暂无域名统计数据" />
        )}
      </Card>

      {/* Worker历史趋势 */}
      <Card title={<Flex align="center" gap={6}><LineChartOutlined style={{ color: token.colorPrimary }} /><span style={{ fontSize: 14 }}>Worker 历史趋势</span></Flex>} extra={<Space><Select placeholder="选择 Worker" style={{ width: 140 }} allowClear value={selectedWorkerId} onChange={setSelectedWorkerId} size="small" options={workers.map(worker => ({ label: worker.name, value: worker.id }))} /><Select value={historyHours} onChange={setHistoryHours} style={{ width: 80 }} size="small" options={[{ label: '1小时', value: 1 }, { label: '6小时', value: 6 }, { label: '24小时', value: 24 }]} /></Space>} style={{ borderRadius: 12 }} styles={{ body: { padding: '12px 16px' } }}>
        <div style={{ height: 220 }}>
          {historyError ? (
            <Alert type="error" showIcon message="历史指标加载失败" description={historyError} />
          ) : selectedWorkerId ? (
            historyData.length > 0 ? (
              <Line data={{
                labels: historyData.map(p => p.timestamp.slice(11, 16)),
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
