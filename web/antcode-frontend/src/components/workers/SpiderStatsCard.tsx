/**
 * 爬虫统计卡片组件 - 现代化设计 + 自动刷新
 */
import type React from 'react'
import { useEffect, useState, memo, useMemo, useCallback, useRef } from 'react'
import { Card, Tag, Skeleton, theme, Flex, Typography, Progress, Tooltip, Button } from 'antd'
import {
  SendOutlined,
  CheckCircleOutlined,
  DatabaseOutlined,
  WarningOutlined,
  FieldTimeOutlined,
  ThunderboltOutlined,
  ApiOutlined,
  SyncOutlined
} from '@ant-design/icons'
import {
  Chart as ChartJS,
  ArcElement,
  Tooltip as ChartTooltip,
  Legend
} from 'chart.js'
import { Doughnut } from 'react-chartjs-2'
import { workerService } from '@/services/workers'
import type { ClusterSpiderStats } from '@/types'
import Logger from '@/utils/logger'

ChartJS.register(ArcElement, ChartTooltip, Legend)

const { Text } = Typography

// 自动刷新间隔
const AUTO_REFRESH_INTERVAL = 10000

interface SpiderStatsCardProps {
  refreshKey?: number
}

// 迷你指标项
const MiniMetric: React.FC<{
  icon: React.ReactNode
  label: string
  value: number | string
  suffix?: string
  color: string
}> = ({ icon, label, value, suffix, color }) => (
  <Flex vertical align="center" gap={4} style={{ flex: 1, minWidth: 80 }}>
    <div style={{
      width: 36,
      height: 36,
      borderRadius: 10,
      background: `${color}15`,
      display: 'flex',
      alignItems: 'center',
      justifyContent: 'center',
      color,
      fontSize: 16
    }}>
      {icon}
    </div>
    <Text style={{ fontSize: 18, fontWeight: 600, color, lineHeight: 1.2 }}>
      {typeof value === 'number' ? value.toLocaleString() : value}
      {suffix && <span style={{ fontSize: 12, fontWeight: 400, marginLeft: 2 }}>{suffix}</span>}
    </Text>
    <Text type="secondary" style={{ fontSize: 11 }}>{label}</Text>
  </Flex>
)

const SpiderStatsCard: React.FC<SpiderStatsCardProps> = memo(({ refreshKey }) => {
  const { token } = theme.useToken()
  const [loading, setLoading] = useState(true)
  const [refreshing, setRefreshing] = useState(false)
  const [stats, setStats] = useState<ClusterSpiderStats | null>(null)
  const [lastUpdate, setLastUpdate] = useState<Date | null>(null)
  const timerRef = useRef<NodeJS.Timeout | null>(null)

  // 加载数据
  const loadStats = useCallback(async (showLoading = false) => {
    if (showLoading) setLoading(true)
    setRefreshing(true)
    try {
      const data = await workerService.getClusterSpiderStats()
      setStats(data)
      setLastUpdate(new Date())
    } catch (e) {
      Logger.error('Failed to load spider stats:', e)
    } finally {
      setLoading(false)
      setRefreshing(false)
    }
  }, [])

  // 手动刷新
  const handleManualRefresh = useCallback(() => {
    loadStats(false)
  }, [loadStats])

  // 初始加载
  useEffect(() => {
    loadStats(true)
  }, [refreshKey, loadStats])

  // 自动刷新
  useEffect(() => {
    timerRef.current = setInterval(() => {
      loadStats(false)
    }, AUTO_REFRESH_INTERVAL)

    return () => {
      if (timerRef.current) {
        clearInterval(timerRef.current)
      }
    }
  }, [loadStats])

  const successRate = stats && stats.totalResponses > 0
    ? ((stats.totalResponses - stats.totalErrors) / stats.totalResponses * 100)
    : 0

  // 迷你环形图数据
  const miniDoughnutData = useMemo(() => {
    if (!stats?.statusCodes || Object.keys(stats.statusCodes).length === 0) return null
    const entries = Object.entries(stats.statusCodes).sort((a, b) => b[1] - a[1]).slice(0, 4)
    return {
      labels: entries.map(([code]) => code),
      datasets: [{
        data: entries.map(([, count]) => count),
        backgroundColor: ['rgba(82,196,26,0.85)', 'rgba(24,144,255,0.85)', 'rgba(250,173,20,0.85)', 'rgba(255,77,79,0.85)'],
        borderWidth: 0,
        cutout: '65%'
      }]
    }
  }, [stats?.statusCodes])

  const miniDoughnutOptions = {
    responsive: true,
    maintainAspectRatio: false,
    plugins: {
      legend: { display: false },
      tooltip: { backgroundColor: 'rgba(0,0,0,0.8)', padding: 8, cornerRadius: 6, titleFont: { size: 11 }, bodyFont: { size: 11 } }
    }
  }

  return (
    <Card
      title={
        <Flex align="center" gap={8}>
          <ApiOutlined style={{ color: token.colorPrimary, fontSize: 18 }} />
          <span style={{ fontWeight: 600 }}>爬虫统计</span>
        </Flex>
      }
      extra={
        <Tooltip title={lastUpdate ? `上次更新: ${lastUpdate.toLocaleTimeString()}，每 ${AUTO_REFRESH_INTERVAL / 1000} 秒自动刷新` : '点击刷新'}>
          <Button
            type="text"
            size="small"
            icon={<SyncOutlined spin={refreshing} />}
            onClick={handleManualRefresh}
            style={{ borderRadius: 8 }}
          >
            {stats?.workerCount || 0} Worker
          </Button>
        </Tooltip>
      }
      style={{ borderRadius: 16, boxShadow: '0 2px 12px rgba(0,0,0,0.06)' }}
      styles={{ body: { padding: '20px 24px' } }}
    >
      <Skeleton loading={loading} active>
        {/* 核心指标 */}
        <Flex gap={8} style={{ marginBottom: 20 }}>
          <MiniMetric icon={<SendOutlined />} label="请求" value={stats?.totalRequests || 0} color="#667eea" />
          <MiniMetric icon={<CheckCircleOutlined />} label="响应" value={stats?.totalResponses || 0} color="#52c41a" />
          <MiniMetric icon={<DatabaseOutlined />} label="数据项" value={stats?.totalItemsScraped || 0} color="#1890ff" />
          <MiniMetric icon={<WarningOutlined />} label="错误" value={stats?.totalErrors || 0} color="#ff4d4f" />
          <MiniMetric icon={<FieldTimeOutlined />} label="延迟" value={stats?.avgLatencyMs?.toFixed(0) || 0} suffix="ms" color="#faad14" />
          <MiniMetric icon={<ThunderboltOutlined />} label="速率" value={stats?.clusterRequestsPerMinute?.toFixed(1) || 0} suffix="/m" color="#722ed1" />
        </Flex>

        {/* 成功率和状态码分布 */}
        <Flex gap={24} align="center" style={{ paddingTop: 16, borderTop: `1px solid ${token.colorBorderSecondary}` }}>
          {/* 成功率进度环 */}
          <Flex align="center" gap={12}>
            <Progress
              type="circle"
              percent={Number(successRate.toFixed(1))}
              size={56}
              strokeWidth={8}
              strokeColor={{
                '0%': successRate >= 95 ? '#52c41a' : successRate >= 80 ? '#faad14' : '#ff4d4f',
                '100%': successRate >= 95 ? '#95de64' : successRate >= 80 ? '#ffc53d' : '#ff7875'
              }}
              format={p => <span style={{ fontSize: 14, fontWeight: 600 }}>{p}%</span>}
            />
            <Flex vertical>
              <Text type="secondary" style={{ fontSize: 12 }}>成功率</Text>
              <Text strong style={{ fontSize: 13, color: successRate >= 95 ? token.colorSuccess : successRate >= 80 ? token.colorWarning : token.colorError }}>
                {successRate >= 95 ? '优秀' : successRate >= 80 ? '良好' : '需关注'}
              </Text>
            </Flex>
          </Flex>

          {/* 状态码分布迷你图 */}
          <Flex align="center" gap={12} style={{ flex: 1 }}>
            <div style={{ width: 56, height: 56 }}>
              {miniDoughnutData ? (
                <Doughnut data={miniDoughnutData} options={miniDoughnutOptions} />
              ) : (
                <div style={{ width: 56, height: 56, borderRadius: '50%', background: token.colorBgContainerDisabled }} />
              )}
            </div>
            <Flex vertical gap={2}>
              <Text type="secondary" style={{ fontSize: 12 }}>状态码分布</Text>
              <Flex gap={4} wrap="wrap">
                {stats?.statusCodes && Object.entries(stats.statusCodes).sort((a, b) => b[1] - a[1]).slice(0, 3).map(([code, count]) => (
                  <Tag
                    key={code}
                    style={{ margin: 0, fontSize: 11, padding: '0 6px', borderRadius: 4 }}
                    color={code.startsWith('2') ? 'success' : code.startsWith('3') ? 'processing' : code.startsWith('4') ? 'warning' : 'error'}
                  >
                    {code}: {count.toLocaleString()}
                  </Tag>
                ))}
              </Flex>
            </Flex>
          </Flex>
        </Flex>
      </Skeleton>
    </Card>
  )
})

export default SpiderStatsCard
