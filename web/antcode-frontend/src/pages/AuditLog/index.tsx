/**
 * 审计日志页面
 */
import type React from 'react'
import { useState, useEffect, useCallback } from 'react'
import {
  Card,
  Table,
  Tag,
  Space,
  Button,
  Form,
  Select,
  DatePicker,
  Input,
  Drawer,
  Descriptions,
  Typography,
  Row,
  Col,
  Statistic,
  Modal,
  InputNumber,
  message
} from 'antd'
import {
  SearchOutlined,
  ReloadOutlined,
  EyeOutlined,
  FileTextOutlined,
  UserOutlined,
  CalendarOutlined,
  DeleteOutlined,
  CheckCircleOutlined,
  CloseCircleOutlined
} from '@ant-design/icons'
import { useAuthStore } from '@/stores/authStore'
import {
  getAuditLogs,
  getAuditStats,
  getAuditActions,
  cleanupAuditLogs,
  type AuditLogItem,
  type AuditLogQueryParams,
  type AuditStats,
  type ActionOption
} from '@/services/audit'
import Logger from '@/utils/logger'
import dayjs from 'dayjs'
import styles from './AuditLog.module.css'

const { Option } = Select
const { RangePicker } = DatePicker
const { Text } = Typography

// 资源类型映射
const RESOURCE_TYPE_LABELS: Record<string, string> = {
  auth: '认证',
  user: '用户',
  project: '项目',
  task: '任务',
  worker: 'Worker',
  config: '配置',
  env: '环境',
  file: '文件'
}

const AuditLog: React.FC = () => {
  const { user } = useAuthStore()
  const [loading, setLoading] = useState(false)
  const [logs, setLogs] = useState<AuditLogItem[]>([])
  const [total, setTotal] = useState(0)
  const [currentPage, setCurrentPage] = useState(1)
  const [pageSize, setPageSize] = useState(50)

  // 选项数据
  const [actions, setActions] = useState<ActionOption[]>([])

  // 统计数据
  const [stats, setStats] = useState<AuditStats | null>(null)

  // 查询表单
  const [searchForm] = Form.useForm()

  // 详情抽屉
  const [detailVisible, setDetailVisible] = useState(false)
  const [selectedLog, setSelectedLog] = useState<AuditLogItem | null>(null)

  // 清理对话框
  const [cleanupVisible, setCleanupVisible] = useState(false)
  const [cleanupDays, setCleanupDays] = useState(90)
  const [cleanupLoading, setCleanupLoading] = useState(false)

  // 检查权限
  const isAdmin = user?.is_admin

  // 加载审计日志
  const loadAuditLogs = useCallback(async (params?: AuditLogQueryParams) => {
    setLoading(true)
    try {
      const queryParams = {
        page: currentPage,
        page_size: pageSize,
        ...params
      }
      const data = await getAuditLogs(queryParams)
      setLogs(data.items)
      setTotal(data.total)
    } catch (error: unknown) {
      const errorMessage = error instanceof Error ? error.message : '未知错误'
      message.error(`加载审计日志失败: ${errorMessage}`)
    } finally {
      setLoading(false)
    }
  }, [currentPage, pageSize])

  // 加载统计数据
  const loadStats = useCallback(async () => {
    try {
      const data = await getAuditStats(7)
      setStats(data)
    } catch (error) {
      Logger.error('加载统计数据失败:', error)
    }
  }, [])

  // 加载选项数据
  const loadOptions = useCallback(async () => {
    try {
      const actionsData = await getAuditActions()
      setActions(actionsData)
    } catch (error) {
      Logger.error('加载选项数据失败:', error)
    }
  }, [])

  useEffect(() => {
    if (isAdmin) {
      loadAuditLogs()
      loadStats()
      loadOptions()
    }
  }, [isAdmin, loadAuditLogs, loadStats, loadOptions])

  // 搜索
  const handleSearch = async () => {
    const values = searchForm.getFieldsValue()
    const params: AuditLogQueryParams = {}

    if (values.action) params.action = values.action
    if (values.resource_type) params.resource_type = values.resource_type
    if (values.username) params.username = values.username
    if (values.success !== undefined) params.success = values.success
    if (values.time_range && values.time_range.length === 2) {
      params.start_date = values.time_range[0].format('YYYY-MM-DD')
      params.end_date = values.time_range[1].format('YYYY-MM-DD')
    }

    setCurrentPage(1)
    await loadAuditLogs(params)
  }

  // 重置搜索
  const handleReset = () => {
    searchForm.resetFields()
    setCurrentPage(1)
    loadAuditLogs()
  }

  // 查看详情
  const handleViewDetail = (log: AuditLogItem) => {
    setSelectedLog(log)
    setDetailVisible(true)
  }

  // 清理日志
  const handleCleanup = async () => {
    setCleanupLoading(true)
    try {
      const result = await cleanupAuditLogs(cleanupDays)
      message.success(`已清理 ${result.deleted} 条旧日志`)
      setCleanupVisible(false)
      loadAuditLogs()
      loadStats()
    } catch (error: unknown) {
      const errorMessage = error instanceof Error ? error.message : '未知错误'
      message.error(`清理失败: ${errorMessage}`)
    } finally {
      setCleanupLoading(false)
    }
  }

  // 表格列定义
  const columns = [
    {
      title: '时间',
      dataIndex: 'created_at',
      key: 'created_at',
      width: 180,
      render: (time: string) => dayjs(time).format('YYYY-MM-DD HH:mm:ss')
    },
    {
      title: '用户',
      dataIndex: 'username',
      key: 'username',
      width: 120,
      render: (username: string) => (
        <Space>
          <UserOutlined />
          {username || '系统'}
        </Space>
      )
    },
    {
      title: '操作',
      dataIndex: 'action',
      key: 'action',
      width: 120,
      render: (action: string) => {
        const actionOption = actions.find(a => a.value === action)
        return actionOption ? actionOption.label : action
      }
    },
    {
      title: '结果',
      dataIndex: 'success',
      key: 'success',
      width: 80,
      render: (success: boolean) => (
        <Tag color={success ? 'success' : 'error'} icon={success ? <CheckCircleOutlined /> : <CloseCircleOutlined />}>
          {success ? '成功' : '失败'}
        </Tag>
      )
    },
    {
      title: '资源',
      key: 'resource',
      width: 150,
      render: (_: unknown, record: AuditLogItem) => {
        if (!record.resource_type) return '-'
        const typeName = RESOURCE_TYPE_LABELS[record.resource_type] || record.resource_type
        return (
          <div>
            <div>{typeName}</div>
            {record.resource_name && (
              <Text type="secondary" style={{ fontSize: '12px' }}>
                {record.resource_name}
              </Text>
            )}
          </div>
        )
      }
    },
    {
      title: 'IP地址',
      dataIndex: 'ip_address',
      key: 'ip_address',
      width: 120
    },
    {
      title: '描述',
      dataIndex: 'description',
      key: 'description',
      ellipsis: true
    },
    {
      title: '操作',
      key: 'actions',
      width: 80,
      render: (_: unknown, record: AuditLogItem) => (
        <Button
          type="link"
          size="small"
          icon={<EyeOutlined />}
          onClick={() => handleViewDetail(record)}
        >
          详情
        </Button>
      )
    }
  ]

  if (!isAdmin) {
    return (
      <div className={styles.noPermission}>
        <FileTextOutlined style={{ fontSize: 64, color: '#ccc' }} />
        <h3>权限不足</h3>
        <p>仅管理员可查看审计日志</p>
      </div>
    )
  }

  return (
    <div className={styles.auditLogContainer}>
      <div className={styles.pageHeader}>
        <h1 className={styles.pageTitle}>
          <FileTextOutlined style={{ marginRight: 8 }} />
          审计日志
        </h1>
        <Space>
          <Button icon={<DeleteOutlined />} onClick={() => setCleanupVisible(true)}>
            清理日志
          </Button>
          <Button icon={<ReloadOutlined />} onClick={() => loadAuditLogs()}>
            刷新
          </Button>
        </Space>
      </div>

      {/* 搜索表单 */}
      <Card size="small" className={styles.searchCard}>
        <Form form={searchForm} layout="inline" onFinish={handleSearch}>
          <Form.Item name="action" label="操作类型">
            <Select placeholder="选择操作类型" allowClear style={{ width: 150 }}>
              {actions.map(action => (
                <Option key={action.value} value={action.value}>
                  {action.label}
                </Option>
              ))}
            </Select>
          </Form.Item>

          <Form.Item name="resource_type" label="资源类型">
            <Select placeholder="选择资源类型" allowClear style={{ width: 120 }}>
              {Object.entries(RESOURCE_TYPE_LABELS).map(([value, label]) => (
                <Option key={value} value={value}>
                  {label}
                </Option>
              ))}
            </Select>
          </Form.Item>

          <Form.Item name="username" label="用户名">
            <Input placeholder="输入用户名" style={{ width: 120 }} />
          </Form.Item>

          <Form.Item name="success" label="结果">
            <Select placeholder="选择结果" allowClear style={{ width: 100 }}>
              <Option value={true}>成功</Option>
              <Option value={false}>失败</Option>
            </Select>
          </Form.Item>

          <Form.Item name="time_range" label="时间范围">
            <RangePicker />
          </Form.Item>

          <Form.Item>
            <Space>
              <Button type="primary" htmlType="submit" icon={<SearchOutlined />}>
                搜索
              </Button>
              <Button onClick={handleReset}>重置</Button>
            </Space>
          </Form.Item>
        </Form>
      </Card>

      {/* 统计信息 */}
      {stats && (
        <Row gutter={16} style={{ marginBottom: 16 }}>
          <Col span={6}>
            <Card size="small">
              <Statistic title="近7天总记录" value={stats.total} />
            </Card>
          </Col>
          <Col span={6}>
            <Card size="small">
              <Statistic
                title="成功率"
                value={stats.success_rate}
                suffix="%"
                valueStyle={{ color: stats.success_rate >= 95 ? '#52c41a' : '#faad14' }}
              />
            </Card>
          </Col>
          <Col span={6}>
            <Card size="small">
              <Statistic
                title="失败操作"
                value={stats.failed_count}
                valueStyle={{ color: stats.failed_count > 0 ? '#ff4d4f' : '#52c41a' }}
              />
            </Card>
          </Col>
          <Col span={6}>
            <Card size="small">
              <Statistic title="当前页记录" value={logs.length} />
            </Card>
          </Col>
        </Row>
      )}

      {/* 审计日志表格 */}
      <Card>
        <Table
          columns={columns}
          dataSource={logs}
          rowKey={(record) => record.id?.toString() || `${record.created_at}-${record.action}-${record.username}`}
          loading={loading}
          pagination={{
            current: currentPage,
            pageSize: pageSize,
            total: total,
            showSizeChanger: true,
            showQuickJumper: true,
            showTotal: (total, range) => `第 ${range[0]}-${range[1]} 条，共 ${total} 条`,
            onChange: (page, size) => {
              setCurrentPage(page)
              setPageSize(size || 50)
            }
          }}
          size="small"
        />
      </Card>

      {/* 详情抽屉 */}
      <Drawer
        title="审计日志详情"
        open={detailVisible}
        onClose={() => setDetailVisible(false)}
        width={600}
      >
        {selectedLog && (
          <Descriptions column={1} bordered size="small">
            <Descriptions.Item label="操作时间">
              <Space>
                <CalendarOutlined />
                {dayjs(selectedLog.created_at).format('YYYY-MM-DD HH:mm:ss')}
              </Space>
            </Descriptions.Item>

            <Descriptions.Item label="用户">
              <Space>
                <UserOutlined />
                {selectedLog.username || '系统'}
              </Space>
            </Descriptions.Item>

            <Descriptions.Item label="操作类型">
              {actions.find(a => a.value === selectedLog.action)?.label || selectedLog.action}
            </Descriptions.Item>

            <Descriptions.Item label="操作结果">
              <Tag color={selectedLog.success ? 'success' : 'error'}>
                {selectedLog.success ? '成功' : '失败'}
              </Tag>
            </Descriptions.Item>

            {selectedLog.resource_type && (
              <Descriptions.Item label="资源类型">
                {RESOURCE_TYPE_LABELS[selectedLog.resource_type] || selectedLog.resource_type}
              </Descriptions.Item>
            )}

            {selectedLog.resource_name && (
              <Descriptions.Item label="资源名称">
                {selectedLog.resource_name}
              </Descriptions.Item>
            )}

            {selectedLog.resource_id && (
              <Descriptions.Item label="资源ID">
                {selectedLog.resource_id}
              </Descriptions.Item>
            )}

            {selectedLog.ip_address && (
              <Descriptions.Item label="IP地址">
                {selectedLog.ip_address}
              </Descriptions.Item>
            )}

            {selectedLog.description && (
              <Descriptions.Item label="描述">
                {selectedLog.description}
              </Descriptions.Item>
            )}

            {selectedLog.error_message && (
              <Descriptions.Item label="错误信息">
                <Text type="danger">{selectedLog.error_message}</Text>
              </Descriptions.Item>
            )}
          </Descriptions>
        )}
      </Drawer>

      {/* 清理对话框 */}
      <Modal
        title="清理审计日志"
        open={cleanupVisible}
        onOk={handleCleanup}
        onCancel={() => setCleanupVisible(false)}
        confirmLoading={cleanupLoading}
      >
        <p>将删除指定天数之前的审计日志，此操作不可恢复。</p>
        <Form.Item label="保留天数">
          <InputNumber
            min={30}
            max={365}
            value={cleanupDays}
            onChange={(value) => setCleanupDays(value || 90)}
          />
          <Text type="secondary" style={{ marginLeft: 8 }}>
            将删除 {cleanupDays} 天之前的日志
          </Text>
        </Form.Item>
      </Modal>
    </div>
  )
}

export default AuditLog
