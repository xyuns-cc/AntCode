/**
 * Worker 管理页面
 * 管理和监控分布式Worker
 */
import React, { useState, useEffect, useMemo, useCallback } from 'react'
import {
  Card,
  Button,
  Space,
  Tag,
  Input,
  Select,
  Tooltip,
  Popconfirm,
  Modal,
  Form,
  Row,
  Col,
  Statistic,
  Progress,
  Badge,
  Typography,
  Descriptions,
  Alert,
  Tabs,
  Dropdown,
  theme,
  message
} from 'antd'
import {
  DeleteOutlined,
  EditOutlined,
  ReloadOutlined,
  ClusterOutlined,
  CheckCircleOutlined,
  CloseCircleOutlined,
  ApiOutlined,
  PlayCircleOutlined,
  LinkOutlined,
  TeamOutlined,
  WindowsOutlined,
  AppleOutlined,
  DesktopOutlined,
  UserAddOutlined,
  UserDeleteOutlined,
  DownOutlined,
  CopyOutlined
} from '@ant-design/icons'
import ResponsiveTable from '@/components/common/ResponsiveTable'
import WorkerResourceManagement from '@/components/workers/WorkerResourceManagement'
import WorkerSpiderStats from '@/components/workers/WorkerSpiderStats'
import { useWorkerStore } from '@/stores/workerStore'
import { workerService } from '@/services/workers'
import { userService } from '@/services/users'
import type { Worker, WorkerStatus } from '@/types'
import { formatDateTime } from '@/utils/format'
import showNotification from '@/utils/notification'
import { STORAGE_KEYS } from '@/utils/constants'

// Worker用户权限类型
interface WorkerUserPermission {
  user_id: string
  username: string
  permission: string
  assigned_at: string
  note?: string
}

const { Search } = Input
const { Option } = Select
const { Text, Paragraph } = Typography

// Worker 状态配置
const statusConfig: Record<WorkerStatus, { color: string; text: string; badge: 'success' | 'error' | 'warning' | 'processing' }> = {
  online: { color: 'success', text: '在线', badge: 'success' },
  offline: { color: 'error', text: '离线', badge: 'error' },
  maintenance: { color: 'warning', text: '维护中', badge: 'warning' },
  connecting: { color: 'processing', text: '连接中', badge: 'processing' }
}

// 获取操作系统图标和名称
const getOsInfo = (osType?: string) => {
  if (!osType) return { icon: <DesktopOutlined />, name: '未知', color: 'default' }
  const os = osType.toLowerCase()
  if (os === 'windows' || os.includes('windows')) {
    return { icon: <WindowsOutlined />, name: 'Windows', color: 'blue' }
  } else if (os === 'darwin' || os.includes('mac')) {
    return { icon: <AppleOutlined />, name: 'macOS', color: 'purple' }
  } else if (os === 'linux' || os.includes('linux')) {
    return { icon: <DesktopOutlined />, name: 'Linux', color: 'orange' }
  }
  return { icon: <DesktopOutlined />, name: osType, color: 'default' }
}

const Workers: React.FC = () => {
  const { token } = theme.useToken()
  const { workers, loading, refreshWorkers, silentRefresh, setCurrentWorker, removeWorker, updateWorker, lastRefreshed } = useWorkerStore()

  // 本地状态
  const [selectedRowKeys, setSelectedRowKeys] = useState<React.Key[]>([])
  const [searchQuery, setSearchQuery] = useState('')
  const [statusFilter, setStatusFilter] = useState<WorkerStatus | undefined>(undefined)
  const [regionFilter, setRegionFilter] = useState<string | undefined>(undefined)
  const [editModalVisible, setEditModalVisible] = useState(false)
  const [detailModalVisible, setDetailModalVisible] = useState(false)
  const [selectedWorker, setSelectedWorker] = useState<Worker | null>(null)
  const [installKeyModalVisible, setInstallKeyModalVisible] = useState(false)
  const [installKeyData, setInstallKeyData] = useState<{
    key: string
    os_type: string
    allowed_source?: string
    install_command: string
    expires_at: string
  } | null>(null)
  const [form] = Form.useForm()

  // 权限管理
  const [permissionModalVisible, setPermissionModalVisible] = useState(false)
  const [workerUsers, setWorkerUsers] = useState<WorkerUserPermission[]>([])
  const [loadingUsers, setLoadingUsers] = useState(false)
  const [allUsers, setAllUsers] = useState<Array<{ id: string; username: string; is_admin?: boolean }>>([])
  const [selectedUserId, setSelectedUserId] = useState<string | undefined>(undefined)

  // 分页
  const [currentPage, setCurrentPage] = useState(1)
  const [pageSize, setPageSize] = useState(10)

  // 初始化加载并设置自动刷新
  useEffect(() => {
    // 首次加载显示loading
    refreshWorkers()

    // 每5秒静默刷新Worker 状态（无感更新）
    const intervalId = setInterval(() => {
      silentRefresh()
    }, 5000)

    return () => clearInterval(intervalId)
  }, [refreshWorkers, silentRefresh])

  // 获取所有 regions
  const regions = useMemo(() => {
    const regionSet = new Set(workers.map(n => n.region).filter(Boolean))
    return Array.from(regionSet) as string[]
  }, [workers])

  // 筛选后的数据
  const filteredWorkers = useMemo(() => {
    let filtered = [...workers]

    if (statusFilter) {
      filtered = filtered.filter(n => n.status === statusFilter)
    }

    if (regionFilter) {
      filtered = filtered.filter(n => n.region === regionFilter)
    }

    if (searchQuery) {
      const query = searchQuery.toLowerCase()
      filtered = filtered.filter(n =>
        n.name.toLowerCase().includes(query) ||
        n.host.toLowerCase().includes(query) ||
        n.description?.toLowerCase().includes(query)
      )
    }

    return filtered
  }, [workers, statusFilter, regionFilter, searchQuery])

  // 分页数据
  const paginatedWorkers = useMemo(() => {
    const start = (currentPage - 1) * pageSize
    return filteredWorkers.slice(start, start + pageSize)
  }, [filteredWorkers, currentPage, pageSize])

  // 统计数据
  const stats = useMemo(() => ({
    total: workers.length,
    online: workers.filter(n => n.status === 'online').length,
    offline: workers.filter(n => n.status === 'offline').length,
    maintenance: workers.filter(n => n.status === 'maintenance').length,
    totalTasks: workers.reduce((sum, n) => sum + (n.metrics?.taskCount || 0), 0),
    runningTasks: workers.reduce((sum, n) => sum + (n.metrics?.runningTasks || 0), 0),
    totalProjects: workers.reduce((sum, n) => sum + (n.metrics?.projectCount || 0), 0),
    avgCpu: workers.length > 0
      ? Math.round(workers.reduce((sum, n) => sum + (n.metrics?.cpu || 0), 0) / workers.length)
      : 0,
    avgMemory: workers.length > 0
      ? Math.round(workers.reduce((sum, n) => sum + (n.metrics?.memory || 0), 0) / workers.length)
      : 0
  }), [workers])

  // 编辑Worker
  const handleEdit = async (values: { name?: string; host?: string; port?: number; region?: string; description?: string; tags?: string[] }) => {
    if (!selectedWorker) return
    try {
      const updated = await workerService.updateWorker(selectedWorker.id, values)
      updateWorker(selectedWorker.id, updated)
      setEditModalVisible(false)
      showNotification('success', 'Worker更新成功')
    } catch (error: unknown) {
      const err = error as { message?: string }
      showNotification('error', err.message || '更新Worker失败')
    }
  }

  // 删除Worker
  const handleDelete = useCallback(async (workerId: string) => {
    try {
      await workerService.deleteWorker(workerId)
      removeWorker(workerId)
      showNotification('success', 'Worker删除成功')
    } catch (error: unknown) {
      const err = error as { message?: string }
      showNotification('error', err.message || '删除Worker失败')
    }
  }, [removeWorker])

  // 批量删除
  const handleBatchDelete = () => {
    if (selectedRowKeys.length === 0) return

    Modal.confirm({
      title: '确认批量删除',
      content: `确定要删除选中的 ${selectedRowKeys.length} 个Worker吗？此操作不可恢复。`,
      okText: '确认删除',
      okType: 'danger',
      cancelText: '取消',
      onOk: async () => {
        let successCount = 0
        for (const id of selectedRowKeys) {
          try {
            await workerService.deleteWorker(id as string)
            removeWorker(id as string)
            successCount++
          } catch {
            // 继续删除其他
          }
        }
        setSelectedRowKeys([])
        showNotification('success', `成功删除 ${successCount} 个Worker`)
      }
    })
  }

  // 测试连接
  const handleTestConnection = useCallback(async (workerId: string) => {
    try {
      const result = await workerService.testConnection(workerId)
      if (result.success) {
        showNotification('success', `连接成功，延迟: ${result.latency}ms`)
        // 刷新Worker 列表以更新状态
        refreshWorkers()
      } else {
        showNotification('error', result.error || '连接失败')
      }
    } catch (error: unknown) {
      const err = error as { message?: string }
      showNotification('error', err.message || '测试连接失败')
    }
  }, [refreshWorkers])

  // 生成安装 Key 并复制到剪贴板
  const handleGenerateKey = async (osType: string) => {
    try {
      const allowedSource = localStorage.getItem(STORAGE_KEYS.INSTALL_KEY_ALLOWED_SOURCE) || undefined
      const result = await workerService.generateInstallKey(osType, allowedSource)
      setInstallKeyData(result)
      setInstallKeyModalVisible(true)
      // 复制 Key 到剪贴板（保持旧行为）
      await navigator.clipboard.writeText(result.key)
      showNotification('success', `${osType.toUpperCase()} 安装 Key 已复制到剪贴板`)
    } catch (error: unknown) {
      const err = error as { message?: string }
      showNotification('error', err.message || '生成安装 Key 失败')
    }
  }


  // 进入 Worker
  const handleEnterWorker = useCallback((worker: Worker) => {
    setCurrentWorker(worker)
    showNotification('success', `已切换到Worker: ${worker.name}`)
  }, [setCurrentWorker])

  // 打开编辑弹窗
  const openEditModal = useCallback((worker: Worker) => {
    setSelectedWorker(worker)
    form.setFieldsValue({
      name: worker.name,
      host: worker.host,
      port: worker.port,
      region: worker.region,
      description: worker.description,
      tags: worker.tags
    })
    setEditModalVisible(true)
  }, [form, setEditModalVisible, setSelectedWorker])

  // 打开详情弹窗
  const openDetailModal = useCallback((worker: Worker) => {
    setSelectedWorker(worker)
    setDetailModalVisible(true)
  }, [setDetailModalVisible, setSelectedWorker])

  // ========== 权限管理 ==========

  // 打开权限管理弹窗
  const openPermissionModal = useCallback(async (worker: Worker) => {
    setSelectedWorker(worker)
    setPermissionModalVisible(true)
    setLoadingUsers(true)

    try {
      // 获取Worker的授权用户
      const users = await workerService.getWorkerUsers(worker.id)
      setWorkerUsers(users)

      // 获取所有用户列表（用于添加权限）- 只获取非管理员用户
      const allUsersData = await userService.getUserList({ page: 1, size: 100 })
      // 过滤掉管理员用户，管理员默认拥有全部Worker 权限
      const regularUsers = allUsersData.users.filter(u => !u.is_admin)
      setAllUsers(regularUsers.map(u => ({ id: u.id, username: u.username })))
    } catch (error: unknown) {
      const err = error as { message?: string }
      showNotification('error', err.message || '获取权限信息失败')
    } finally {
      setLoadingUsers(false)
    }
  }, [setAllUsers, setLoadingUsers, setPermissionModalVisible, setSelectedWorker, setWorkerUsers])

  // 分配权限
  const handleAssignPermission = async () => {
    if (!selectedWorker || !selectedUserId) {
      showNotification('warning', '请选择用户')
      return
    }

    try {
      await workerService.assignWorkerToUser(selectedWorker.id, selectedUserId, 'use')
      showNotification('success', '权限分配成功')

      // 刷新用户列表
      const users = await workerService.getWorkerUsers(selectedWorker.id)
      setWorkerUsers(users)
      setSelectedUserId(undefined)
    } catch (error: unknown) {
      const err = error as { message?: string }
      showNotification('error', err.message || '分配权限失败')
    }
  }

  // 撤销权限
  const handleRevokePermission = async (userId: string, username: string) => {
    if (!selectedWorker) return

    Modal.confirm({
      title: '撤销权限',
      content: `确定要撤销用户 "${username}" 对此Worker的访问权限吗？`,
      okText: '撤销',
      okType: 'danger',
      cancelText: '取消',
      onOk: async () => {
        try {
          await workerService.revokeWorkerFromUser(selectedWorker.id, userId)
          showNotification('success', '权限已撤销')

          // 刷新用户列表
          const users = await workerService.getWorkerUsers(selectedWorker.id)
          setWorkerUsers(users)
        } catch (error: unknown) {
          const err = error as { message?: string }
          showNotification('error', err.message || '撤销权限失败')
        }
      }
    })
  }

  // 获取未分配权限的用户
  const availableUsers = useMemo(() => {
    const assignedUserIds = new Set(workerUsers.map(u => String(u.user_id)))
    return allUsers.filter(u => !assignedUserIds.has(String(u.id)))
  }, [allUsers, workerUsers])

  // 表格列
  const columns = useMemo(() => [
    {
      title: 'Worker 名称',
      dataIndex: 'name',
      key: 'name',
      width: 180,
      render: (name: string, record: Worker) => (
        <Space>
          <Badge status={statusConfig[record.status].badge} />
          <Button type="link" onClick={() => openDetailModal(record)} style={{ padding: 0 }}>
            {name}
          </Button>
        </Space>
      )
    },
    {
      title: '连接模式',
      key: 'transportMode',
      width: 120,
      render: (_: unknown, record: Worker) => {
        const isDirect = record.transportMode === 'direct'

        if (isDirect) {
          return (
            <Tag color="cyan">Direct</Tag>
          )
        }

        // Gateway 模式：鼠标悬停显示地址和复制按钮
        const address = `${record.host}:${record.port}`
        return (
          <Tooltip
            title={
              <Space>
                <span>Worker 地址: {address}</span>
                <CopyOutlined
                  style={{ cursor: 'pointer', color: '#1890ff' }}
                  onClick={(e) => {
                    e.stopPropagation()
                    navigator.clipboard.writeText(address)
                    message.success('地址已复制')
                  }}
                />
              </Space>
            }
          >
            <Tag color="blue" style={{ cursor: 'pointer' }}>
              Gateway
            </Tag>
          </Tooltip>
        )
      }
    },
    {
      title: '状态',
      dataIndex: 'status',
      key: 'status',
      width: 100,
      render: (status: WorkerStatus) => (
        <Tag color={statusConfig[status].color}>{statusConfig[status].text}</Tag>
      )
    },
    {
      title: '区域',
      dataIndex: 'region',
      key: 'region',
      width: 100,
      render: (region: string) => region ? <Tag color="blue">{region}</Tag> : '-'
    },
    {
      title: '系统',
      key: 'os',
      width: 120,
      render: (_: unknown, record: Worker) => {
        const osInfo = getOsInfo(record.osType)
        return record.osType ? (
          <Tooltip title={`${record.osVersion || ''} ${record.machineArch ? `(${record.machineArch})` : ''}`} placement="topLeft">
            <Tag
              color={osInfo.color}
              style={{ display: 'inline-flex', alignItems: 'center', gap: '4px' }}
            >
              {React.cloneElement(osInfo.icon, { style: { fontSize: 12 } })}
              <span>{osInfo.name}</span>
            </Tag>
          </Tooltip>
        ) : '-'
      }
    },
    {
      title: 'CPU',
      key: 'cpu',
      width: 100,
      render: (_: unknown, record: Worker) => (
        record.metrics?.cpu !== undefined ? (
          <Progress
            percent={Math.round(record.metrics.cpu)}
            size="small"
            status={record.metrics.cpu > 80 ? 'exception' : 'normal'}
          />
        ) : '-'
      )
    },
    {
      title: '内存',
      key: 'memory',
      width: 100,
      render: (_: unknown, record: Worker) => (
        record.metrics?.memory !== undefined ? (
          <Progress
            percent={Math.round(record.metrics.memory)}
            size="small"
            status={record.metrics.memory > 80 ? 'exception' : 'normal'}
          />
        ) : '-'
      )
    },
    {
      title: '任务',
      key: 'tasks',
      width: 80,
      render: (_: unknown, record: Worker) => (
        record.metrics ? (
          <Tooltip title={`运行中: ${record.metrics.runningTasks}`} placement="topLeft">
            <span>{record.metrics.runningTasks}/{record.metrics.taskCount}</span>
          </Tooltip>
        ) : '-'
      )
    },
    {
      title: '渲染',
      key: 'render',
      width: 80,
      render: (_: unknown, record: Worker) => {
        return record.capabilities?.drissionpage?.enabled ? (
          <Tag color="green">有</Tag>
        ) : (
          <Tag color="default">无</Tag>
        )
      }
    },
    {
      title: '最后心跳',
      dataIndex: 'lastHeartbeat',
      key: 'lastHeartbeat',
      width: 160,
      render: (time: string) => time ? formatDateTime(time) : '-'
    },
    {
      title: '操作',
      key: 'actions',
      width: 240,
      render: (_: unknown, record: Worker) => (
        <Space size="small">
          <Tooltip title="进入 Worker" placement="top">
            <Button
              type="link"
              size="small"
              icon={<PlayCircleOutlined />}
              onClick={() => handleEnterWorker(record)}
              disabled={record.status !== 'online'}
            />
          </Tooltip>
          <Tooltip title="测试连接" placement="top">
            <Button
              type="link"
              size="small"
              icon={<ApiOutlined />}
              onClick={() => handleTestConnection(record.id)}
            />
          </Tooltip>
          <Tooltip title="权限管理" placement="top">
            <Button
              type="link"
              size="small"
              icon={<TeamOutlined />}
              onClick={() => openPermissionModal(record)}
            />
          </Tooltip>
          <Tooltip title="编辑" placement="top">
            <Button
              type="link"
              size="small"
              icon={<EditOutlined />}
              onClick={() => openEditModal(record)}
            />
          </Tooltip>
          <Popconfirm
            title="确定删除此Worker？"
            onConfirm={() => handleDelete(record.id)}
            okText="删除"
            cancelText="取消"
          >
            <Tooltip title="删除" placement="top">
              <Button
                type="link"
                size="small"
                danger
                icon={<DeleteOutlined />}
              />
            </Tooltip>
          </Popconfirm>
        </Space>
      )
    }
  ], [
    handleDelete,
    handleEnterWorker,
    handleTestConnection,
    openDetailModal,
    openEditModal,
    openPermissionModal
  ])

  return (
    <div style={{ padding: '24px' }}>
      {/* 页面标题 */}
      <div style={{ marginBottom: '24px' }}>
        <h1 style={{ fontSize: '24px', fontWeight: 'bold', margin: 0, display: 'flex', alignItems: 'center', gap: '8px' }}>
          <ClusterOutlined />
          Worker 管理
        </h1>
        <p style={{ margin: '8px 0 0 0', opacity: 0.65 }}>
          管理和监控分布式 Worker
        </p>
      </div>

      {/* 统计卡片 */}
      <Row gutter={16} style={{ marginBottom: 16 }}>
        <Col xs={12} sm={8} md={6} lg={4}>
          <Card size="small">
            <Statistic
              title="总Worker"
              value={stats.total}
              prefix={<ClusterOutlined />}
            />
          </Card>
        </Col>
        <Col xs={12} sm={8} md={6} lg={4}>
          <Card size="small">
            <Statistic
              title="在线"
              value={stats.online}
              valueStyle={{ color: token.colorSuccess }}
              prefix={<CheckCircleOutlined />}
            />
          </Card>
        </Col>
        <Col xs={12} sm={8} md={6} lg={4}>
          <Card size="small">
            <Statistic
              title="离线"
              value={stats.offline}
              valueStyle={{ color: stats.offline > 0 ? '#ff4d4f' : undefined }}
              prefix={<CloseCircleOutlined />}
            />
          </Card>
        </Col>
        <Col xs={12} sm={8} md={6} lg={4}>
          <Card size="small">
            <Statistic
              title="总任务"
              value={stats.totalTasks}
              suffix={<Text type="secondary" style={{ fontSize: 12 }}>/ {stats.runningTasks} 运行中</Text>}
            />
          </Card>
        </Col>
        <Col xs={12} sm={8} md={6} lg={4}>
          <Card size="small">
            <Statistic
              title="平均 CPU"
              value={stats.avgCpu}
              suffix="%"
              valueStyle={{ color: stats.avgCpu > 80 ? '#ff4d4f' : undefined }}
            />
          </Card>
        </Col>
        <Col xs={12} sm={8} md={6} lg={4}>
          <Card size="small">
            <Statistic
              title="平均内存"
              value={stats.avgMemory}
              suffix="%"
              valueStyle={{ color: stats.avgMemory > 80 ? '#ff4d4f' : undefined }}
            />
          </Card>
        </Col>
      </Row>

      {/* 工具栏 */}
      <Card style={{ marginBottom: 16 }}>
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', flexWrap: 'wrap', gap: '12px' }}>
          <Space wrap size="middle">
            <Space>
              <Badge status="processing" />
              <Text type="secondary" style={{ fontSize: 12 }}>
                实时监控
                {lastRefreshed && (
                  <span style={{ marginLeft: 4, opacity: 0.7 }}>
                    · {new Date(lastRefreshed).toLocaleTimeString()}
                  </span>
                )}
              </Text>
            </Space>
            <Button
              icon={<ReloadOutlined />}
              onClick={() => refreshWorkers()}
              loading={loading}
              size="small"
            >
              刷新
            </Button>
            <Dropdown
              menu={{
                items: [
                  { key: 'linux', label: 'Linux', icon: <DesktopOutlined /> },
                  { key: 'macos', label: 'macOS', icon: <AppleOutlined /> },
                  { key: 'windows', label: 'Windows', icon: <WindowsOutlined /> },
                ],
                onClick: ({ key }) => handleGenerateKey(key)
              }}
              trigger={['hover']}
            >
              <Button type="primary" icon={<LinkOutlined />}>
                连接 Worker <DownOutlined />
              </Button>
            </Dropdown>
            <Button
              danger
              icon={<DeleteOutlined />}
              disabled={selectedRowKeys.length === 0}
              onClick={handleBatchDelete}
            >
              批量删除{selectedRowKeys.length > 0 && ` (${selectedRowKeys.length})`}
            </Button>
          </Space>
          <Space wrap size="middle">
            <Select
              placeholder="状态"
              allowClear
              style={{ width: 100 }}
              value={statusFilter}
              onChange={setStatusFilter}
            >
              <Option value="online">在线</Option>
              <Option value="offline">离线</Option>
              <Option value="maintenance">维护中</Option>
            </Select>
            <Select
              placeholder="区域"
              allowClear
              style={{ width: 120 }}
              value={regionFilter}
              onChange={setRegionFilter}
            >
              {regions.map(r => (
                <Option key={r} value={r}>{r}</Option>
              ))}
            </Select>
            <Search
              placeholder="搜索Worker"
              allowClear
              style={{ width: 200 }}
              value={searchQuery}
              onChange={e => setSearchQuery(e.target.value)}
            />
          </Space>
        </div>
      </Card>

      {/* Worker表格 */}
      <Card>
        <ResponsiveTable
          dataSource={paginatedWorkers}
          columns={columns}
          rowKey="id"
          loading={loading}
          rowSelection={{
            selectedRowKeys,
            onChange: setSelectedRowKeys
          }}
          pagination={{
            current: currentPage,
            pageSize: pageSize,
            total: filteredWorkers.length,
            showSizeChanger: true,
            showQuickJumper: true,
            showTotal: (total, range) => `第 ${range[0]}-${range[1]} 条，共 ${total} 条`,
            onChange: (page, size) => {
              setCurrentPage(page)
              if (size !== pageSize) setPageSize(size)
            }
          }}
          minWidth={1000}
        />
      </Card>

      {/* 编辑弹窗 */}
      <Modal
        title="编辑Worker"
        open={editModalVisible}
        onCancel={() => {
          setEditModalVisible(false)
          form.resetFields()
        }}
        footer={null}
        width={600}
        forceRender
      >
        <Form
          form={form}
          layout="vertical"
          onFinish={handleEdit}
        >
          <Row gutter={16}>
            <Col span={12}>
              <Form.Item
                name="name"
                label="Worker 名称"
                rules={[{ required: true, message: '请输入Worker 名称' }]}
              >
                <Input placeholder="例如：Worker-001" />
              </Form.Item>
            </Col>
            <Col span={12}>
              <Form.Item
                name="region"
                label="区域"
              >
                <Input placeholder="例如：华东、华北" />
              </Form.Item>
            </Col>
          </Row>
          {/* 仅 Gateway 模式显示主机地址和端口 */}
          {selectedWorker?.transportMode !== 'direct' && (
            <Row gutter={16}>
              <Col span={16}>
                <Form.Item
                  name="host"
                  label="主机地址"
                  rules={[{ required: true, message: '请输入主机地址' }]}
                >
                  <Input placeholder="例如：192.168.1.100 或 worker1.example.com" />
                </Form.Item>
              </Col>
              <Col span={8}>
                <Form.Item
                  name="port"
                  label="端口"
                  rules={[{ required: true, message: '请输入端口' }]}
                  initialValue={8000}
                >
                  <Input type="number" placeholder="8000" />
                </Form.Item>
              </Col>
            </Row>
          )}
          <Form.Item
            name="description"
            label="描述"
          >
            <Input.TextArea rows={3} placeholder="Worker 描述（可选）" />
          </Form.Item>
          <Form.Item>
            <Space>
              <Button type="primary" htmlType="submit">
                保存
              </Button>
              <Button onClick={() => setEditModalVisible(false)}>
                取消
              </Button>
            </Space>
          </Form.Item>
        </Form>
      </Modal>

      {/* Worker 详情弹窗 */}
      <Modal
        title={
          <Space>
            <ClusterOutlined />
            Worker 详情
            {selectedWorker && <Tag color="blue">{selectedWorker.name}</Tag>}
          </Space>
        }
        open={detailModalVisible}
        onCancel={() => setDetailModalVisible(false)}
        footer={
          <Space>
            <Button onClick={() => setDetailModalVisible(false)}>关闭</Button>
            {selectedWorker?.status === 'online' && (
              <Button type="primary" onClick={() => {
                if (selectedWorker) handleEnterWorker(selectedWorker)
                setDetailModalVisible(false)
              }}>
                进入 Worker
              </Button>
            )}
          </Space>
        }
        width={900}
      >
        {selectedWorker && (
          <Tabs
            defaultActiveKey="info"
            items={[
              {
                key: 'info',
                label: '基本信息',
                children: (
                  <Descriptions
                    column={2}
                    bordered
                    size="small"
                    labelStyle={{ width: 120, whiteSpace: 'nowrap' }}
                    contentStyle={{ minWidth: 200 }}
                  >
                    <Descriptions.Item label="Worker 名称">{selectedWorker.name}</Descriptions.Item>
                    <Descriptions.Item label="状态">
                      <Tag color={statusConfig[selectedWorker.status].color}>
                        {statusConfig[selectedWorker.status].text}
                      </Tag>
                    </Descriptions.Item>
                    <Descriptions.Item label="连接模式">
                      <Tag color={selectedWorker.transportMode === 'direct' ? 'cyan' : 'blue'}>
                        {selectedWorker.transportMode === 'direct' ? 'Direct' : 'Gateway'}
                      </Tag>
                    </Descriptions.Item>
                    {selectedWorker.transportMode !== 'direct' && selectedWorker.host && (
                      <Descriptions.Item label="地址">
                        <Text code>{selectedWorker.host}:{selectedWorker.port}</Text>
                      </Descriptions.Item>
                    )}
                    <Descriptions.Item label="区域">{selectedWorker.region || '-'}</Descriptions.Item>

                    {/* 操作系统信息 */}
                    <Descriptions.Item label="操作系统">
                      {selectedWorker.osType ? (
                        (() => {
                          const osInfo = getOsInfo(selectedWorker.osType)
                          return (
                            <Tag
                              color={osInfo.color}
                              style={{ display: 'inline-flex', alignItems: 'center', gap: '4px' }}
                            >
                              {React.cloneElement(osInfo.icon, { style: { fontSize: 12 } })}
                              <span>{osInfo.name}{selectedWorker.osVersion ? ` ${selectedWorker.osVersion}` : ''}</span>
                            </Tag>
                          )
                        })()
                      ) : '-'}
                    </Descriptions.Item>
                    <Descriptions.Item label="CPU 架构">
                      {selectedWorker.machineArch ? (
                        <Tag color="cyan">{selectedWorker.machineArch}</Tag>
                      ) : '-'}
                    </Descriptions.Item>
                    <Descriptions.Item label="Python 版本">
                      {selectedWorker.pythonVersion ? (
                        <Tag color="green">Python {selectedWorker.pythonVersion}</Tag>
                      ) : '-'}
                    </Descriptions.Item>
                    <Descriptions.Item label="Worker 版本">{selectedWorker.version || '-'}</Descriptions.Item>

                    {/* 渲染能力 */}
                    <Descriptions.Item label="渲染能力" span={2}>
                      {selectedWorker.capabilities?.drissionpage?.enabled ? (
                        <Tag color="green">有</Tag>
                      ) : (
                        <Tag color="default">无</Tag>
                      )}
                    </Descriptions.Item>

                    <Descriptions.Item label="描述" span={2}>{selectedWorker.description || '-'}</Descriptions.Item>
                    <Descriptions.Item label="最后心跳" span={2}>
                      {selectedWorker.lastHeartbeat ? formatDateTime(selectedWorker.lastHeartbeat) : '-'}
                    </Descriptions.Item>
                    {selectedWorker.metrics && (
                      <>
                        <Descriptions.Item label="CPU 使用率">
                          <Tooltip
                            title={
                              <div>
                                <div>使用率: {selectedWorker.metrics.cpu.toFixed(1)}%</div>
                                {selectedWorker.metrics.cpuCores && (
                                  <div>核心数: {selectedWorker.metrics.cpuCores} 核</div>
                                )}
                              </div>
                            }
                          >
                            <div style={{ cursor: 'pointer' }}>
                              <Progress percent={Math.round(selectedWorker.metrics.cpu)} size="small" />
                            </div>
                          </Tooltip>
                        </Descriptions.Item>
                        <Descriptions.Item label="内存使用率">
                          <Tooltip
                            title={
                              <div>
                                <div>使用率: {selectedWorker.metrics.memory.toFixed(1)}%</div>
                                {selectedWorker.metrics.memoryTotal && (
                                  <>
                                    <div>总内存: {(selectedWorker.metrics.memoryTotal / 1024 / 1024 / 1024).toFixed(2)} GB</div>
                                    <div>已使用: {((selectedWorker.metrics.memoryUsed || 0) / 1024 / 1024 / 1024).toFixed(2)} GB</div>
                                    <div>可用: {((selectedWorker.metrics.memoryAvailable || 0) / 1024 / 1024 / 1024).toFixed(2)} GB</div>
                                  </>
                                )}
                              </div>
                            }
                          >
                            <div style={{ cursor: 'pointer' }}>
                              <Progress percent={Math.round(selectedWorker.metrics.memory)} size="small" />
                            </div>
                          </Tooltip>
                        </Descriptions.Item>
                        <Descriptions.Item label="磁盘使用率">
                          <Tooltip
                            title={
                              <div>
                                <div>使用率: {selectedWorker.metrics.disk.toFixed(1)}%</div>
                                {selectedWorker.metrics.diskTotal && (
                                  <>
                                    <div>总容量: {(selectedWorker.metrics.diskTotal / 1024 / 1024 / 1024).toFixed(2)} GB</div>
                                    <div>已使用: {((selectedWorker.metrics.diskUsed || 0) / 1024 / 1024 / 1024).toFixed(2)} GB</div>
                                    <div>可用: {((selectedWorker.metrics.diskFree || 0) / 1024 / 1024 / 1024).toFixed(2)} GB</div>
                                  </>
                                )}
                              </div>
                            }
                          >
                            <div style={{ cursor: 'pointer' }}>
                              <Progress percent={Math.round(selectedWorker.metrics.disk)} size="small" />
                            </div>
                          </Tooltip>
                        </Descriptions.Item>
                        <Descriptions.Item label="运行时间">
                          {Math.floor((selectedWorker.metrics.uptime || 0) / 3600)} 小时
                        </Descriptions.Item>
                        <Descriptions.Item label="项目数">{selectedWorker.metrics.projectCount}</Descriptions.Item>
                        <Descriptions.Item label="环境数">{selectedWorker.metrics.envCount}</Descriptions.Item>
                        <Descriptions.Item label="总任务">{selectedWorker.metrics.taskCount}</Descriptions.Item>
                        <Descriptions.Item label="运行中任务">{selectedWorker.metrics.runningTasks}</Descriptions.Item>
                      </>
                    )}
                    <Descriptions.Item label="创建时间" span={2}>
                      {formatDateTime(selectedWorker.createdAt)}
                    </Descriptions.Item>
                  </Descriptions>
                )
              },
              {
                key: 'resources',
                label: '资源管理',
                children: selectedWorker.status === 'online' ? (
                  <WorkerResourceManagement workerId={selectedWorker.id} workerName={selectedWorker.name} />
                ) : (
                  <Alert
                    message="Worker 离线"
                    description="Worker 当前处于离线状态，无法管理资源配置。请确保Worker在线后再试。"
                    type="warning"
                    showIcon
                  />
                )
              },
              {
                key: 'spider',
                label: '爬虫统计',
                children: (
                  <WorkerSpiderStats
                    workerId={selectedWorker.id}
                    workerName={selectedWorker.name}
                    workerStatus={selectedWorker.status}
                  />
                )
              }
            ]}
          />
        )}
      </Modal>

      {/* 安装 Key 弹窗 */}
      <Modal
        title={
          <Space>
            <LinkOutlined />
            Worker 安装 Key
          </Space>
        }
        open={installKeyModalVisible}
        onCancel={() => setInstallKeyModalVisible(false)}
        footer={
          <Space>
            <Button
              onClick={async () => {
                if (!installKeyData?.install_command) return
                try {
                  await navigator.clipboard.writeText(installKeyData.install_command)
                  showNotification('success', '安装命令已复制到剪贴板')
                } catch (error: unknown) {
                  const err = error as { message?: string }
                  showNotification('error', err.message || '复制安装命令失败')
                }
              }}
              disabled={!installKeyData?.install_command}
            >
              复制安装命令
            </Button>
            <Button type="primary" onClick={() => setInstallKeyModalVisible(false)}>
              关闭
            </Button>
          </Space>
        }
        width={720}
      >
        {installKeyData && (
          <Space direction="vertical" size="middle" style={{ width: '100%' }}>
            <Alert
              type="info"
              showIcon
              message="Gateway 首次连接需要安装 Key"
              description="Direct 模式无需安装 Key，会自动生成并持久化 worker_id。"
            />
            <Descriptions
              column={1}
              bordered
              size="small"
              labelStyle={{ width: 140, whiteSpace: 'nowrap' }}
            >
              <Descriptions.Item label="操作系统">
                {installKeyData.os_type?.toUpperCase?.() || '-'}
              </Descriptions.Item>
              <Descriptions.Item label="来源绑定">
                {installKeyData.allowed_source || '-'}
              </Descriptions.Item>
              <Descriptions.Item label="有效期">
                {installKeyData.expires_at ? formatDateTime(installKeyData.expires_at) : '-'}
              </Descriptions.Item>
            </Descriptions>
            <div>
              <Text type="secondary">安装 Key</Text>
              <Paragraph copyable={{ text: installKeyData.key }} style={{ marginBottom: 0 }}>
                <Text code style={{ wordBreak: 'break-all' }}>{installKeyData.key}</Text>
              </Paragraph>
            </div>
            <div>
              <Text type="secondary">安装命令</Text>
              <Paragraph copyable={{ text: installKeyData.install_command }} style={{ marginBottom: 0 }}>
                <Text code style={{ whiteSpace: 'pre-wrap', wordBreak: 'break-all' }}>
                  {installKeyData.install_command}
                </Text>
              </Paragraph>
            </div>
          </Space>
        )}
      </Modal>

      {/* 权限管理弹窗 */}
      <Modal
        title={
          <Space>
            <TeamOutlined />
            Worker 权限管理
            {selectedWorker && <Tag color="blue">{selectedWorker.name}</Tag>}
          </Space>
        }
        open={permissionModalVisible}
        onCancel={() => {
          setPermissionModalVisible(false)
          setSelectedUserId(undefined)
        }}
        footer={
          <Button onClick={() => setPermissionModalVisible(false)}>关闭</Button>
        }
        width={600}
      >
        {/* 添加用户权限 */}
        <Card size="small" title="添加用户权限" style={{ marginBottom: 16 }}>
          <Space style={{ width: '100%' }}>
            <Select
              placeholder="选择用户"
              style={{ width: 200 }}
              value={selectedUserId}
              onChange={setSelectedUserId}
              showSearch
              optionFilterProp="children"
              loading={loadingUsers}
            >
              {availableUsers.map(user => (
                <Option key={user.id} value={user.id}>{user.username}</Option>
              ))}
            </Select>
            <Button
              type="primary"
              icon={<UserAddOutlined />}
              onClick={handleAssignPermission}
              disabled={!selectedUserId}
            >
              分配权限
            </Button>
          </Space>
          {availableUsers.length === 0 && !loadingUsers && (
            <Text type="secondary" style={{ display: 'block', marginTop: 8 }}>
              所有用户都已分配权限
            </Text>
          )}
        </Card>

        {/* 已授权用户列表 */}
        <Card size="small" title={`已授权用户 (${workerUsers.length})`}>
          {loadingUsers ? (
            <div style={{ textAlign: 'center', padding: 20 }}>加载中...</div>
          ) : workerUsers.length === 0 ? (
            <div style={{ textAlign: 'center', padding: 20, color: token.colorTextTertiary }}>
              暂无授权用户
            </div>
          ) : (
            <div style={{ maxHeight: 300, overflow: 'auto' }}>
              {workerUsers.map(user => (
                <div
                  key={user.user_id}
                  style={{
                    display: 'flex',
                    justifyContent: 'space-between',
                    alignItems: 'center',
                    padding: '8px 12px',
                    borderBottom: '1px solid #f0f0f0'
                  }}
                >
                  <Space>
                    <Badge status="success" />
                    <Text strong>{user.username}</Text>
                    <Tag color="green">{user.permission === 'use' ? '可使用' : '只读'}</Tag>
                  </Space>
                  <Space>
                    <Text type="secondary" style={{ fontSize: 12 }}>
                      {user.assigned_at ? formatDateTime(user.assigned_at) : ''}
                    </Text>
                    <Tooltip title="撤销权限" placement="top">
                      <Button
                        type="text"
                        danger
                        size="small"
                        icon={<UserDeleteOutlined />}
                        onClick={() => handleRevokePermission(user.user_id, user.username)}
                      />
                    </Tooltip>
                  </Space>
                </div>
              ))}
            </div>
          )}
        </Card>
      </Modal>

    </div>
  )
}

export default Workers
