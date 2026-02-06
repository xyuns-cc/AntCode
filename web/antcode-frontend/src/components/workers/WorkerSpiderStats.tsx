/**
 * Worker 爬虫统计组件
 * 用于 Worker 详情弹窗的爬虫统计 Tab
 */
import type React from 'react'
import { useEffect, useState, memo } from 'react'
import { Row, Col, Statistic, Tag, Alert, Skeleton, theme, Flex, Typography, Card, Progress } from 'antd'
import {
  SendOutlined,
  CheckCircleOutlined,
  DatabaseOutlined,
  WarningOutlined,
  FieldTimeOutlined,
  ThunderboltOutlined
} from '@ant-design/icons'
import { workerService } from '@/services/workers'
import type { SpiderStatsSummary } from '@/types'
import Logger from '@/utils/logger'

const { Text } = Typography

interface WorkerSpiderStatsProps {
  workerId: string
  workerName?: string  // 保留用于未来扩展
  workerStatus: string
}

const WorkerSpiderStats: React.FC<WorkerSpiderStatsProps> = memo(({ workerId, workerStatus }) => {
  const { token } = theme.useToken()
  const [loading, setLoading] = useState(true)
  const [stats, setStats] = useState<SpiderStatsSummary | null>(null)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    if (workerStatus !== 'online') {
      setLoading(false)
      return
    }

    const loadStats = async () => {
      setLoading(true)
      setError(null)
      try {
        const data = await workerService.getWorkerSpiderStats(workerId)
        setStats(data)
      } catch (e) {
        Logger.error('Failed to load worker spider stats:', e)
        setError('获取爬虫统计失败')
      } finally {
        setLoading(false)
      }
    }
    loadStats()
  }, [workerId, workerStatus])

  if (workerStatus !== 'online') {
    return (
      <Alert
        message="Worker 离线"
        description="Worker 当前处于离线状态，无法获取爬虫统计。请确保Worker在线后再试。"
        type="warning"
        showIcon
      />
    )
  }

  if (error) {
    return (
      <Alert
        message="加载失败"
        description={error}
        type="error"
        showIcon
      />
    )
  }

  // 计算成功率
  const successRate = stats && stats.responseCount > 0
    ? ((stats.responseCount - stats.errorCount) / stats.responseCount * 100).toFixed(1)
    : '0.0'

  // 状态码分布
  const statusCodeEntries = stats?.statusCodes
    ? Object.entries(stats.statusCodes).sort((a, b) => b[1] - a[1])
    : []

  // 计算各类状态码占比
  const totalCodes = statusCodeEntries.reduce((sum, [, count]) => sum + count, 0)
  const successCodes = statusCodeEntries.filter(([code]) => code.startsWith('2')).reduce((sum, [, count]) => sum + count, 0)
  const clientErrors = statusCodeEntries.filter(([code]) => code.startsWith('4')).reduce((sum, [, count]) => sum + count, 0)
  const serverErrors = statusCodeEntries.filter(([code]) => code.startsWith('5')).reduce((sum, [, count]) => sum + count, 0)

  return (
    <Skeleton loading={loading} active paragraph={{ rows: 6 }}>
      <div style={{ padding: '8px 0' }}>
        {/* 核心指标 */}
        <Row gutter={[16, 16]}>
          <Col span={8}>
            <Card size="small" style={{ textAlign: 'center' }}>
              <Statistic
                title="请求总数"
                value={stats?.requestCount || 0}
                prefix={<SendOutlined />}
                valueStyle={{ color: token.colorPrimary }}
              />
            </Card>
          </Col>
          <Col span={8}>
            <Card size="small" style={{ textAlign: 'center' }}>
              <Statistic
                title="响应总数"
                value={stats?.responseCount || 0}
                prefix={<CheckCircleOutlined />}
                valueStyle={{ color: token.colorSuccess }}
              />
            </Card>
          </Col>
          <Col span={8}>
            <Card size="small" style={{ textAlign: 'center' }}>
              <Statistic
                title="抓取数据项"
                value={stats?.itemScrapedCount || 0}
                prefix={<DatabaseOutlined />}
                valueStyle={{ color: token.colorInfo }}
              />
            </Card>
          </Col>
          <Col span={8}>
            <Card size="small" style={{ textAlign: 'center' }}>
              <Statistic
                title="错误数"
                value={stats?.errorCount || 0}
                prefix={<WarningOutlined />}
                valueStyle={{ color: stats?.errorCount ? token.colorError : token.colorTextSecondary }}
              />
            </Card>
          </Col>
          <Col span={8}>
            <Card size="small" style={{ textAlign: 'center' }}>
              <Statistic
                title="平均延迟"
                value={stats?.avgLatencyMs?.toFixed(1) || 0}
                suffix="ms"
                prefix={<FieldTimeOutlined />}
                valueStyle={{ color: (stats?.avgLatencyMs || 0) > 1000 ? token.colorWarning : token.colorSuccess }}
              />
            </Card>
          </Col>
          <Col span={8}>
            <Card size="small" style={{ textAlign: 'center' }}>
              <Statistic
                title="请求速率"
                value={stats?.requestsPerMinute?.toFixed(1) || 0}
                suffix="/min"
                prefix={<ThunderboltOutlined />}
                valueStyle={{ color: token.purple }}
              />
            </Card>
          </Col>
        </Row>

        {/* 成功率和状态码分布 */}
        <Card size="small" style={{ marginTop: 16 }} title="响应分析">
          <Row gutter={24}>
            <Col span={8}>
              <Flex vertical align="center" gap={8}>
                <Text type="secondary">成功率</Text>
                <Progress
                  type="circle"
                  percent={Number(successRate)}
                  size={80}
                  strokeColor={Number(successRate) >= 95 ? token.colorSuccess : Number(successRate) >= 80 ? token.colorWarning : token.colorError}
                  format={(percent) => `${percent}%`}
                />
              </Flex>
            </Col>
            <Col span={16}>
              <Text type="secondary" style={{ display: 'block', marginBottom: 12 }}>状态码分布</Text>
              {totalCodes > 0 ? (
                <Flex vertical gap={8}>
                  <Flex justify="space-between" align="center">
                    <Text>2xx 成功</Text>
                    <Flex align="center" gap={8}>
                      <Progress
                        percent={Math.round(successCodes / totalCodes * 100)}
                        size="small"
                        style={{ width: 120 }}
                        strokeColor={token.colorSuccess}
                        showInfo={false}
                      />
                      <Text style={{ minWidth: 60, textAlign: 'right' }}>{successCodes}</Text>
                    </Flex>
                  </Flex>
                  <Flex justify="space-between" align="center">
                    <Text>4xx 客户端错误</Text>
                    <Flex align="center" gap={8}>
                      <Progress
                        percent={Math.round(clientErrors / totalCodes * 100)}
                        size="small"
                        style={{ width: 120 }}
                        strokeColor={token.colorWarning}
                        showInfo={false}
                      />
                      <Text style={{ minWidth: 60, textAlign: 'right' }}>{clientErrors}</Text>
                    </Flex>
                  </Flex>
                  <Flex justify="space-between" align="center">
                    <Text>5xx 服务端错误</Text>
                    <Flex align="center" gap={8}>
                      <Progress
                        percent={Math.round(serverErrors / totalCodes * 100)}
                        size="small"
                        style={{ width: 120 }}
                        strokeColor={token.colorError}
                        showInfo={false}
                      />
                      <Text style={{ minWidth: 60, textAlign: 'right' }}>{serverErrors}</Text>
                    </Flex>
                  </Flex>
                </Flex>
              ) : (
                <Text type="secondary">暂无状态码数据</Text>
              )}
            </Col>
          </Row>
        </Card>

        {/* 详细状态码 */}
        {statusCodeEntries.length > 0 && (
          <Card size="small" style={{ marginTop: 16 }} title="状态码详情">
            <Flex wrap="wrap" gap={8}>
              {statusCodeEntries.map(([code, count]) => (
                <Tag
                  key={code}
                  color={code.startsWith('2') ? 'success' : code.startsWith('3') ? 'processing' : code.startsWith('4') ? 'warning' : code.startsWith('5') ? 'error' : 'default'}
                >
                  {code}: {count}
                </Tag>
              ))}
            </Flex>
          </Card>
        )}

        {/* 无数据提示 */}
        {stats && stats.requestCount === 0 && (
          <Alert
            message="暂无爬虫统计数据"
            description="该Worker尚未执行爬虫任务，或统计数据已被清空。"
            type="info"
            showIcon
            style={{ marginTop: 16 }}
          />
        )}
      </div>
    </Skeleton>
  )
})

export default WorkerSpiderStats
