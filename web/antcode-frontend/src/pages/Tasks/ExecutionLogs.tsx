import type React from 'react'
import { useState, useEffect, useCallback, useMemo } from 'react'
import { useParams, useNavigate } from 'react-router'
import PageContainer from '@/components/common/PageContainer'
import {
  Card,
  Button,
  Space,
  Empty,
  Tag,
  Row,
  Col,
  Skeleton,
  Alert,
  Tooltip,
  Progress,
  Typography,
  theme,
} from 'antd'
import showNotification from '@/utils/notification'
import {
  ArrowLeftOutlined,
  ReloadOutlined,
  InfoCircleOutlined,
  CheckCircleOutlined,
  CloseCircleOutlined,
  ClockCircleOutlined,
  ThunderboltOutlined,
} from '@ant-design/icons'
import EnhancedLogViewer from '@/components/ui/LogViewer/EnhancedLogViewer'
import CopyableTooltip from '@/components/common/CopyableTooltip'
import { taskService } from '@/services/tasks'
import { runsService, type RunArtifact } from '@/services/runs'
import type { Task, TaskExecution } from '@/types'
import { formatDateTime, formatDuration } from '@/utils/format'
import Logger from '@/utils/logger'
import { isTerminalTaskStatus } from '@/utils/taskStatus'
import { DownloadOutlined, FileOutlined } from '@ant-design/icons'
import { List } from 'antd'
import { SpiderItemsCard } from './components/SpiderItemsCard'

const { Title, Text } = Typography

// 浏览器端仅保留最新 N 行（后端历史回放上限更大）；截断时查看器会显式提示。
const LOG_VIEWER_MAX_LINES = 5000

const ExecutionLogs: React.FC = () => {
  const navigate = useNavigate()
  const { taskId, runId } = useParams<{ taskId: string; runId: string }>()
  const { token } = theme.useToken()

  // 状态管理
  const [task, setTask] = useState<Task | null>(null)
  const [execution, setExecution] = useState<TaskExecution | null>(null)
  const [loading, setLoading] = useState(true)
  const [refreshing, setRefreshing] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [viewerHeight, setViewerHeight] = useState(600)

  // L2 产物链：从 /api/v1/runs/{run_id}/artifacts 拉列表；执行结束再取一次
  const [artifacts, setArtifacts] = useState<RunArtifact[]>([])
  const [artifactsLoading, setArtifactsLoading] = useState(false)
  // P1-round6 5.4: 失败必须区别于"确实无数据",UI 展示"加载失败"而非空。
  const [artifactsError, setArtifactsError] = useState<string | null>(null)
  const [downloading, setDownloading] = useState<string | null>(null)

  // 加载任务信息
  const loadTask = useCallback(async () => {
    if (!taskId) return

    try {
      const taskData = await taskService.getTask(taskId)
      setTask(taskData)
    } catch (error: unknown) {
      Logger.error('加载任务信息失败:', error)
      // 不显示错误，因为主要关注执行日志
    }
  }, [taskId])

  // 加载执行信息
  // P1-round6 5.4: 返回 boolean 让 refreshExecution 判断真实结果, 避免"内部
  // setError 但外层仍 notify 刷新成功"; 同时校验 execution.task_id === URL.taskId
  // 防越权(URL 换 taskId 但 runId 是别人的会渲染别的 task 的日志)。
  const loadExecution = useCallback(async (): Promise<boolean> => {
    if (!taskId || !runId) {
      setError('缺少任务ID或运行ID')
      setLoading(false)
      return false
    }

    try {
      // P2-04：用精确的 getTaskRun(runId) 直接取该 run，之前列表默认 size=20，
      // 第 21 次以前的历史链接会错误显示"未找到执行记录"。
      const exec = await taskService.getTaskRun(runId)
      if (!exec) {
        setError(`未找到执行记录: ${runId}`)
        return false
      }
      // 归属校验: exec.task_id 必须与 URL taskId 匹配, 否则拒渲染
      const execTaskId = String((exec as { task_id?: unknown }).task_id ?? '')
      if (execTaskId && execTaskId !== taskId) {
        setError(`运行 ${runId} 不属于任务 ${taskId}`)
        return false
      }
      setExecution(exec)
      setError(null)
      return true
    } catch (error: unknown) {
      Logger.error('加载执行信息失败:', error)
      const errMsg = error instanceof Error ? error.message : '加载执行信息失败'
      setError(errMsg)
      return false
    } finally {
      setLoading(false)
    }
  }, [taskId, runId])

  // 刷新执行状态
  const refreshExecution = useCallback(async () => {
    setRefreshing(true)
    try {
      const ok = await loadExecution()
      showNotification(ok ? 'success' : 'error', ok ? '执行状态已刷新' : '刷新失败')
    } finally {
      setRefreshing(false)
    }
  }, [loadExecution])

  // L2: 从后端拉产物列表；执行终态触发再拉一次
  const loadArtifacts = useCallback(async () => {
    if (!runId) return
    setArtifactsLoading(true)
    try {
      const items = await runsService.listArtifacts(runId)
      setArtifacts(items)
      setArtifactsError(null)
    } catch (err) {
      Logger.warn('获取产物列表失败', err)
      setArtifacts([])
      // P1-round6 5.4: 失败展示"加载失败"而非空, 避免用户误认为无产物
      setArtifactsError(err instanceof Error ? err.message : '获取产物列表失败')
    } finally {
      setArtifactsLoading(false)
    }
  }, [runId])

  const handleDownloadArtifact = useCallback(
    async (artifactName: string) => {
      if (!runId) return
      setDownloading(artifactName)
      try {
        await runsService.downloadArtifact(runId, artifactName)
      } catch (err) {
        Logger.error('下载产物失败', err)
        showNotification('error', `下载失败: ${(err as Error).message || '未知错误'}`)
      } finally {
        setDownloading(null)
      }
    },
    [runId]
  )

  // 初始加载
  useEffect(() => {
    const init = async () => {
      setLoading(true)
      await Promise.all([loadTask(), loadExecution(), loadArtifacts()])
    }
    init()
  }, [loadTask, loadExecution, loadArtifacts])

  // 执行进入终态时刷新产物列表（写库有延迟）
  useEffect(() => {
    if (execution && isTerminalTaskStatus(execution.status)) {
      loadArtifacts()
    }
  }, [execution, loadArtifacts])

  // 自动刷新（如果任务正在运行）
  useEffect(() => {
    if (!execution || isTerminalTaskStatus(execution.status)) {
      return
    }

    const interval = setInterval(() => {
      loadExecution()
    }, 10000) // 每10秒刷新一次

    return () => clearInterval(interval)
  }, [execution, loadExecution])

  // 根据视口高度动态调整日志查看器高度
  useEffect(() => {
    const updateHeight = () => {
      if (typeof window === 'undefined') return
      const viewportHeight = window.innerHeight || 800
      const reserved = execution ? 420 : 320
      setViewerHeight(Math.max(viewportHeight - reserved, 360))
    }

    updateHeight()
    window.addEventListener('resize', updateHeight)

    return () => {
      window.removeEventListener('resize', updateHeight)
    }
  }, [execution])

  // 计算执行进度（如果有持续时间）
  const executionProgress = useMemo(() => {
    if (!execution) return null

    if (execution.status === 'success') return 100
    if (execution.status === 'failed') return 100
    if (!execution.start_time) return 0

    const startTime = new Date(execution.start_time).getTime()
    const now = Date.now()
    const elapsed = (now - startTime) / 1000 // 秒

    // 假设一般任务在5分钟内完成
    const estimatedDuration = 300
    const progress = Math.min((elapsed / estimatedDuration) * 100, 99)

    return Math.round(progress)
  }, [execution])

  const statusMeta = useMemo(() => {
    const base = {
      tagColor: 'default' as 'default' | 'success' | 'error' | 'processing' | 'warning',
      text: execution ? execution.status?.toUpperCase() || '未知状态' : '等待执行',
      icon: <InfoCircleOutlined style={{ color: token.colorInfo }} />,
    }

    if (!execution) {
      return base
    }

    switch (execution.status) {
      case 'success':
        return {
          tagColor: 'success' as const,
          text: '执行成功',
          icon: <CheckCircleOutlined style={{ color: token.colorSuccess }} />,
        }
      case 'failed':
        return {
          tagColor: 'error' as const,
          text: '执行失败',
          icon: <CloseCircleOutlined style={{ color: token.colorError }} />,
        }
      case 'running':
        return {
          tagColor: 'processing' as const,
          text: '运行中',
          icon: <ClockCircleOutlined style={{ color: token.colorWarning }} spin />,
        }
      default:
        return {
          ...base,
          text: execution.status?.toUpperCase() || '未知状态',
        }
    }
  }, [execution, token.colorError, token.colorInfo, token.colorSuccess, token.colorWarning])

  const durationDisplay = useMemo(() => {
    if (!execution) return '-'
    if (execution.duration_seconds) return formatDuration(execution.duration_seconds)
    if (execution.start_time) {
      const seconds = Math.max(
        0,
        Math.floor((Date.now() - new Date(execution.start_time).getTime()) / 1000)
      )
      return `${formatDuration(seconds)}（进行中）`
    }
    return '-'
  }, [execution])

  const endTimeDisplay = useMemo(() => {
    if (!execution) return '-'
    if (!execution.end_time) {
      return (
        <Tag
          color="processing"
          style={{ display: 'inline-flex', alignItems: 'center', gap: '4px' }}
        >
          <ClockCircleOutlined spin style={{ fontSize: 12 }} />
          <span>运行中</span>
        </Tag>
      )
    }
    return formatDateTime(execution.end_time)
  }, [execution])

  const overviewItems = useMemo(
    () =>
      execution
        ? [
            {
              key: 'runId',
              label: '运行ID',
              value: (
                <CopyableTooltip text={execution.run_id}>
                  <code style={{ cursor: 'pointer' }}>{execution.run_id}</code>
                </CopyableTooltip>
              ),
            },
            {
              key: 'startTime',
              label: '开始时间',
              value: formatDateTime(execution.start_time),
            },
            {
              key: 'endTime',
              label: '结束时间',
              value: endTimeDisplay,
            },
            {
              key: 'duration',
              label: '持续时间',
              value: durationDisplay,
            },
            {
              key: 'exitCode',
              label: '退出码',
              value:
                execution.exit_code !== null && execution.exit_code !== undefined ? (
                  <Tag color={execution.exit_code === 0 ? 'success' : 'error'}>
                    {execution.exit_code}
                  </Tag>
                ) : (
                  '-'
                ),
            },
            {
              key: 'taskId',
              label: '任务ID',
              value: taskId ? `#${taskId}` : '-',
            },
          ]
        : [],
    [durationDisplay, endTimeDisplay, execution, taskId]
  )

  // 如果正在加载
  if (loading) {
    return (
      <div
        style={{
          padding: '24px',
          maxWidth: '1800px',
          margin: '0 auto',
        }}
      >
        <Card>
          <Skeleton active paragraph={{ rows: 10 }} />
        </Card>
      </div>
    )
  }

  // 如果有错误
  if (error && !execution) {
    return (
      <div style={{ padding: '24px' }}>
        <Card>
          <div style={{ textAlign: 'center', padding: '40px' }}>
            <CloseCircleOutlined
              style={{ fontSize: 48, color: token.colorError, marginBottom: 16 }}
            />
            <h3>加载失败</h3>
            <p style={{ color: token.colorTextSecondary, marginBottom: 24 }}>{error}</p>
            <Space>
              <Button icon={<ArrowLeftOutlined />} onClick={() => navigate(`/tasks/${taskId}`)}>
                返回任务详情
              </Button>
              <Button
                type="primary"
                icon={<ReloadOutlined />}
                onClick={() => {
                  setError(null)
                  setLoading(true)
                  loadExecution()
                }}
              >
                重试
              </Button>
            </Space>
          </div>
        </Card>
      </div>
    )
  }

  return (
    <PageContainer scrollable>
      {/* 页面头部 - 更紧凑的设计 */}
      <div
        style={{
          marginBottom: '20px',
          padding: '16px 24px',
          background: token.colorBgContainer,
          borderRadius: '8px',
          boxShadow: '0 1px 2px 0 rgba(0, 0, 0, 0.03), 0 1px 6px -1px rgba(0, 0, 0, 0.02)',
          border: `1px solid ${token.colorBorderSecondary}`,
        }}
      >
        <Row justify="space-between" align="middle" gutter={[16, 12]}>
          <Col xs={24} lg={18}>
            <Space size="middle" wrap>
              <Button
                icon={<ArrowLeftOutlined />}
                onClick={() => navigate(`/tasks/${taskId}`)}
                size="large"
              >
                返回
              </Button>
              <div
                style={{
                  borderLeft: `2px solid ${token.colorBorderSecondary}`,
                  paddingLeft: 16,
                  height: 36,
                  display: 'flex',
                  flexDirection: 'column',
                  justifyContent: 'center',
                }}
              >
                <Title level={5} style={{ margin: 0, lineHeight: 1.2 }}>
                  执行日志
                </Title>
                <Text type="secondary" style={{ fontSize: 12 }}>
                  {task ? task.name : '加载中...'}
                </Text>
              </div>
              <Tag
                color={statusMeta.tagColor}
                icon={statusMeta.icon}
                style={{
                  margin: 0,
                  padding: '4px 12px',
                  fontSize: 13,
                  display: 'flex',
                  alignItems: 'center',
                  gap: 6,
                  border: 'none',
                }}
              >
                {statusMeta.text}
              </Tag>
            </Space>
          </Col>
          <Col xs={24} lg={6} style={{ textAlign: 'right' }}>
            <Button
              icon={<ReloadOutlined />}
              onClick={refreshExecution}
              loading={refreshing}
              type="default"
            >
              刷新状态
            </Button>
          </Col>
        </Row>
      </div>

      {/* 错误提示 */}
      {error && (
        <Alert
          message="加载警告"
          description={error}
          type="warning"
          showIcon
          closable
          style={{ marginBottom: '20px' }}
          onClose={() => setError(null)}
        />
      )}

      {/* 执行概览 - 优化为更现代的卡片式布局 */}
      {execution && (
        <div
          style={{
            marginBottom: '20px',
            padding: '24px',
            background: token.colorBgContainer,
            borderRadius: '8px',
            boxShadow: '0 1px 2px 0 rgba(0, 0, 0, 0.03), 0 1px 6px -1px rgba(0, 0, 0, 0.02)',
            border: `1px solid ${token.colorBorderSecondary}`,
          }}
        >
          {/* 标题区域 */}
          <div
            style={{
              display: 'flex',
              justifyContent: 'space-between',
              alignItems: 'center',
              marginBottom: 20,
              paddingBottom: 16,
              borderBottom: `1px solid ${token.colorBorderSecondary}`,
            }}
          >
            <Space>
              <ThunderboltOutlined style={{ fontSize: 18, color: token.colorPrimary }} />
              <Text strong style={{ fontSize: 16 }}>
                执行详情
              </Text>
            </Space>
            {execution.status === 'running' && executionProgress !== null && (
              <Tooltip title={`预估进度 ${executionProgress}%`} placement="topLeft">
                <Progress
                  type="circle"
                  percent={executionProgress}
                  width={44}
                  format={(percent) => (
                    <span style={{ fontSize: 12, fontWeight: 600 }}>{percent}%</span>
                  )}
                />
              </Tooltip>
            )}
          </div>

          {/* 信息网格 */}
          <Row gutter={[20, 20]}>
            {overviewItems.map((item) => {
              const valueContent =
                typeof item.value === 'string' || typeof item.value === 'number' ? (
                  <Text strong style={{ fontSize: 15 }}>
                    {item.value}
                  </Text>
                ) : (
                  item.value
                )

              return (
                <Col key={item.key} xs={24} sm={12} lg={8}>
                  <div
                    style={{
                      padding: '16px 18px',
                      borderRadius: 6,
                      background: token.colorFillQuaternary,
                      border: `1px solid ${token.colorBorder}`,
                      transition: 'all 0.2s',
                      cursor: 'default',
                      height: '100%',
                    }}
                    onMouseEnter={(e) => {
                      e.currentTarget.style.background = token.colorFillTertiary
                      e.currentTarget.style.borderColor = token.colorPrimaryBorder
                    }}
                    onMouseLeave={(e) => {
                      e.currentTarget.style.background = token.colorFillQuaternary
                      e.currentTarget.style.borderColor = token.colorBorder
                    }}
                  >
                    <div
                      style={{
                        fontSize: 12,
                        color: token.colorTextSecondary,
                        marginBottom: 8,
                        fontWeight: 500,
                      }}
                    >
                      {item.label}
                    </div>
                    <div
                      style={{
                        fontSize: 15,
                        fontWeight: 600,
                        color: token.colorText,
                        lineHeight: 1.4,
                      }}
                    >
                      {valueContent}
                    </div>
                  </div>
                </Col>
              )
            })}
          </Row>
        </div>
      )}

      {/* 日志查看器 */}
      {runId ? (
        <div
          style={{
            background: token.colorBgContainer,
            borderRadius: '8px',
            overflow: 'hidden',
            boxShadow: '0 1px 2px 0 rgba(0, 0, 0, 0.03), 0 1px 6px -1px rgba(0, 0, 0, 0.02)',
            border: `1px solid ${token.colorBorderSecondary}`,
          }}
        >
          <EnhancedLogViewer
            key={runId}
            runId={runId}
            height={viewerHeight}
            showControls={true}
            autoConnect={true}
            showStdout={true}
            showStderr={true}
            maxLines={LOG_VIEWER_MAX_LINES}
            enableSearch={true}
            enableExport={true}
            enableVirtualization={true}
            onStatusUpdate={(statusUpdate) => {
              // 收到状态更新时，更新执行状态
              Logger.info('收到执行状态更新', statusUpdate)
              if (execution && statusUpdate.status) {
                setExecution((prev) =>
                  prev
                    ? {
                        ...prev,
                        status: statusUpdate.status as TaskExecution['status'],
                      }
                    : null
                )
              }
              // 如果任务完成，刷新完整的执行信息
              if (isTerminalTaskStatus(statusUpdate.status)) {
                loadExecution()
              }
            }}
          />
        </div>
      ) : (
        <Card>
          <div
            style={{
              padding: '80px 40px',
              textAlign: 'center',
            }}
          >
            <Empty description="运行ID不存在" image={Empty.PRESENTED_IMAGE_SIMPLE} />
          </div>
        </Card>
      )}

      {runId && <SpiderItemsCard runId={runId} status={execution?.status} />}

      {/* L2: 产物区 —— 执行有产物时展示，允许下载。执行时段可能为空。 */}
      {runId && (
        <Card
          title={
            <Space>
              <FileOutlined />
              <span>执行产物</span>
              {artifacts.length > 0 && <Tag color="blue">{artifacts.length}</Tag>}
            </Space>
          }
          style={{ marginTop: 16 }}
          extra={
            <Button
              size="small"
              icon={<ReloadOutlined />}
              onClick={loadArtifacts}
              loading={artifactsLoading}
            >
              刷新
            </Button>
          }
        >
          {artifactsError && artifacts.length === 0 ? (
            <Alert type="error" showIcon message="产物列表加载失败" description={artifactsError} />
          ) : artifacts.length === 0 ? (
            <Empty
              description={
                artifactsLoading ? '加载中...' : '该执行没有产物（或任务未收集 artifact_patterns）'
              }
              image={Empty.PRESENTED_IMAGE_SIMPLE}
            />
          ) : (
            <List
              itemLayout="horizontal"
              dataSource={artifacts}
              renderItem={(artifact) => (
                <List.Item
                  actions={[
                    <Button
                      key="download"
                      type="link"
                      size="small"
                      icon={<DownloadOutlined />}
                      loading={downloading === artifact.name}
                      onClick={() => handleDownloadArtifact(artifact.name)}
                    >
                      下载
                    </Button>,
                  ]}
                >
                  <List.Item.Meta
                    avatar={<FileOutlined style={{ fontSize: 18, color: token.colorPrimary }} />}
                    title={
                      <Tooltip title={`SHA256: ${artifact.checksum || '未知'}`}>
                        <Text code style={{ fontSize: 13 }}>
                          {artifact.name}
                        </Text>
                      </Tooltip>
                    }
                    description={
                      <Space size="middle">
                        <Text type="secondary" style={{ fontSize: 12 }}>
                          {artifact.mime_type || 'application/octet-stream'}
                        </Text>
                        <Text type="secondary" style={{ fontSize: 12 }}>
                          {formatBytes(artifact.size_bytes)}
                        </Text>
                        {artifact.created_at && (
                          <Text type="secondary" style={{ fontSize: 12 }}>
                            {formatDateTime(artifact.created_at)}
                          </Text>
                        )}
                      </Space>
                    }
                  />
                </List.Item>
              )}
            />
          )}
        </Card>
      )}
    </PageContainer>
  )
}

function formatBytes(bytes: number): string {
  if (!bytes) return '0 B'
  const units = ['B', 'KB', 'MB', 'GB', 'TB']
  let value = bytes
  let idx = 0
  while (value >= 1024 && idx < units.length - 1) {
    value /= 1024
    idx += 1
  }
  return `${value.toFixed(idx === 0 ? 0 : 2)} ${units[idx]}`
}

export default ExecutionLogs
