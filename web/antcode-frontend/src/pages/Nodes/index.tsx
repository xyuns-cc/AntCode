/**
 * 节点管理页面
 * 管理和监控分布式节点
 */
import React, { useState, useEffect, useMemo } from 'react'
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
  Checkbox,
  Tabs,
  theme
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
  SwapOutlined,
  WindowsOutlined,
  AppleOutlined,
  DesktopOutlined,
  UserAddOutlined
} from '@ant-design/icons'
import ResponsiveTable from '@/components/common/ResponsiveTable'
import CopyableTooltip from '@/components/common/CopyableTooltip'
import NodeEnvManagement from '@/components/nodes/NodeEnvManagement'
import NodeResourceManagement from '@/components/nodes/NodeResourceManagement'
import { useNodeStore } from '@/stores/nodeStore'
import { nodeService } from '@/services/nodes'
import { userService } from '@/services/users'
import type { Node, NodeStatus } from '@/types'
import { formatDateTime } from '@/utils/format'
import showNotification from '@/utils/notification'

// 节点用户权限类型
interface NodeUserPermission {
  user_id: string
  username: string
  permission: string
  assigned_at: string
  note?: string
}

const { Search } = Input
const { Option } = Select
const { Text } = Typography

// 节点状态配置
const statusConfig: Record<NodeStatus, { color: string; text: string; badge: 'success' | 'error' | 'warning' | 'processing' }> = {
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

const Nodes: React.FC = () => {
  const { token } = theme.useToken()
  const { nodes, loading, refreshNodes, silentRefresh, setCurrentNode, removeNode, addNode, updateNode, lastRefreshed } = useNodeStore()
  
  // 本地状态
  const [selectedRowKeys, setSelectedRowKeys] = useState<React.Key[]>([])
  const [searchQuery, setSearchQuery] = useState('')
  const [statusFilter, setStatusFilter] = useState<NodeStatus | undefined>(undefined)
  const [regionFilter, setRegionFilter] = useState<string | undefined>(undefined)
  const [connectModalVisible, setConnectModalVisible] = useState(false)
  const [editModalVisible, setEditModalVisible] = useState(false)
  const [detailModalVisible, setDetailModalVisible] = useState(false)
  const [selectedNode, setSelectedNode] = useState<Node | null>(null)
  const [connecting, setConnecting] = useState(false)
  const [form] = Form.useForm()
  const [connectForm] = Form.useForm()

  // 权限管理
  const [permissionModalVisible, setPermissionModalVisible] = useState(false)
  const [nodeUsers, setNodeUsers] = useState<NodeUserPermission[]>([])
  const [loadingUsers, setLoadingUsers] = useState(false)
  const [allUsers, setAllUsers] = useState<Array<{ id: string; username: string; is_admin?: boolean }>>([])
  const [selectedUserId, setSelectedUserId] = useState<string | undefined>(undefined)
  
  // 重新绑定机器码相关
  const [rebindModalVisible, setRebindModalVisible] = useState(false)
  const [rebindForm] = Form.useForm()
  const [rebinding, setRebinding] = useState(false)

  // 分页
  const [currentPage, setCurrentPage] = useState(1)
  const [pageSize, setPageSize] = useState(10)

  // 初始化加载并设置自动刷新
  useEffect(() => {
    // 首次加载显示loading
    refreshNodes()
    
    // 每5秒静默刷新节点状态（无感更新）
    const intervalId = setInterval(() => {
      silentRefresh()
    }, 5000)
    
    return () => clearInterval(intervalId)
  }, [refreshNodes, silentRefresh])

  // 获取所有 regions
  const regions = useMemo(() => {
    const regionSet = new Set(nodes.map(n => n.region).filter(Boolean))
    return Array.from(regionSet) as string[]
  }, [nodes])

  // 筛选后的数据
  const filteredNodes = useMemo(() => {
    let filtered = [...nodes]

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
  }, [nodes, statusFilter, regionFilter, searchQuery])

  // 分页数据
  const paginatedNodes = useMemo(() => {
    const start = (currentPage - 1) * pageSize
    return filteredNodes.slice(start, start + pageSize)
  }, [filteredNodes, currentPage, pageSize])

  // 统计数据
  const stats = useMemo(() => ({
    total: nodes.length,
    online: nodes.filter(n => n.status === 'online').length,
    offline: nodes.filter(n => n.status === 'offline').length,
    maintenance: nodes.filter(n => n.status === 'maintenance').length,
    totalTasks: nodes.reduce((sum, n) => sum + (n.metrics?.taskCount || 0), 0),
    runningTasks: nodes.reduce((sum, n) => sum + (n.metrics?.runningTasks || 0), 0),
    totalProjects: nodes.reduce((sum, n) => sum + (n.metrics?.projectCount || 0), 0),
    avgCpu: nodes.length > 0 
      ? Math.round(nodes.reduce((sum, n) => sum + (n.metrics?.cpu || 0), 0) / nodes.length)
      : 0,
    avgMemory: nodes.length > 0
      ? Math.round(nodes.reduce((sum, n) => sum + (n.metrics?.memory || 0), 0) / nodes.length)
      : 0
  }), [nodes])

  // 编辑节点
  const handleEdit = async (values: { name?: string; host?: string; port?: number; region?: string; description?: string; tags?: string[] }) => {
    if (!selectedNode) return
    try {
      const updated = await nodeService.updateNode(selectedNode.id, values)
      updateNode(selectedNode.id, updated)
      setEditModalVisible(false)
      showNotification('success', '节点更新成功')
    } catch (error: unknown) {
      const err = error as { message?: string }
      showNotification('error', err.message || '更新节点失败')
    }
  }

  // 删除节点
  const handleDelete = async (nodeId: string) => {
    try {
      await nodeService.deleteNode(nodeId)
      removeNode(nodeId)
      showNotification('success', '节点删除成功')
    } catch (error: unknown) {
      const err = error as { message?: string }
      showNotification('error', err.message || '删除节点失败')
    }
  }

  // 批量删除
  const handleBatchDelete = () => {
    if (selectedRowKeys.length === 0) return

    Modal.confirm({
      title: '确认批量删除',
      content: `确定要删除选中的 ${selectedRowKeys.length} 个节点吗？此操作不可恢复。`,
      okText: '确认删除',
      okType: 'danger',
      cancelText: '取消',
      onOk: async () => {
        let successCount = 0
        for (const id of selectedRowKeys) {
          try {
            await nodeService.deleteNode(id as string)
            removeNode(id as string)
            successCount++
          } catch {
            // 继续删除其他
          }
        }
        setSelectedRowKeys([])
        showNotification('success', `成功删除 ${successCount} 个节点`)
      }
    })
  }

  // 测试连接
  const handleTestConnection = async (nodeId: string) => {
    try {
      const result = await nodeService.testConnection(nodeId)
      if (result.success) {
        showNotification('success', `连接成功，延迟: ${result.latency}ms`)
        // 刷新节点列表以更新状态
        refreshNodes()
      } else {
        showNotification('error', result.error || '连接失败')
      }
    } catch (error: unknown) {
      const err = error as { message?: string }
      showNotification('error', err.message || '测试连接失败')
    }
  }

  // 打开重新绑定弹窗
  const openRebindModal = (node: Node) => {
    setSelectedNode(node)
    rebindForm.resetFields()
    setRebindModalVisible(true)
  }

  // 重新绑定机器码
  const handleRebind = async () => {
    try {
      const values = await rebindForm.validateFields()
      setRebinding(true)
      
      await nodeService.rebindNode(selectedNode!.id, {
        new_machine_code: values.new_machine_code,
        verify_connection: values.verify_connection ?? true
      })
      
      showNotification('success', '机器码已更新')
      setRebindModalVisible(false)
      refreshNodes()
    } catch (error: unknown) {
      const err = error as { message?: string }
      showNotification('error', err.message || '重新绑定失败')
    } finally {
      setRebinding(false)
    }
  }

  // 连接节点（通过地址和机器码）
  const handleConnectNode = async (values: { host: string; port: number; machine_code: string }) => {
    setConnecting(true)
    try {
      const newNode = await nodeService.connectNode(values)
      addNode(newNode)
      setConnectModalVisible(false)
      connectForm.resetFields()
      showNotification('success', `节点 ${newNode.name} 连接成功`)
    } catch (error: unknown) {
      const err = error as { message?: string }
      showNotification('error', err.message || '连接节点失败')
    } finally {
      setConnecting(false)
    }
  }

  // 进入节点
  const handleEnterNode = (node: Node) => {
    setCurrentNode(node)
    showNotification('success', `已切换到节点: ${node.name}`)
  }

  // 打开编辑弹窗
  const openEditModal = (node: Node) => {
    setSelectedNode(node)
    form.setFieldsValue({
      name: node.name,
      host: node.host,
      port: node.port,
      region: node.region,
      description: node.description,
      tags: node.tags
    })
    setEditModalVisible(true)
  }

  // 打开详情弹窗
  const openDetailModal = (node: Node) => {
    setSelectedNode(node)
    setDetailModalVisible(true)
  }

  // ========== 权限管理 ==========
  
  // 打开权限管理弹窗
  const openPermissionModal = async (node: Node) => {
    setSelectedNode(node)
    setPermissionModalVisible(true)
    setLoadingUsers(true)
    
    try {
      // 获取节点的授权用户
      const users = await nodeService.getNodeUsers(node.id)
      setNodeUsers(users)
      
      // 获取所有用户列表（用于添加权限）- 只获取非管理员用户
      const allUsersData = await userService.getUserList({ page: 1, size: 100 })
      // 过滤掉管理员用户，管理员默认拥有全部节点权限
      const regularUsers = allUsersData.users.filter(u => !u.is_admin)
      setAllUsers(regularUsers.map(u => ({ id: u.id, username: u.username })))
    } catch (error: unknown) {
      const err = error as { message?: string }
      showNotification('error', err.message || '获取权限信息失败')
    } finally {
      setLoadingUsers(false)
    }
  }

  // 分配权限
  const handleAssignPermission = async () => {
    if (!selectedNode || !selectedUserId) {
      showNotification('warning', '请选择用户')
      return
    }
    
    try {
      await nodeService.assignNodeToUser(selectedNode.id, selectedUserId, 'use')
      showNotification('success', '权限分配成功')
      
      // 刷新用户列表
      const users = await nodeService.getNodeUsers(selectedNode.id)
      setNodeUsers(users)
      setSelectedUserId(undefined)
    } catch (error: unknown) {
      const err = error as { message?: string }
      showNotification('error', err.message || '分配权限失败')
    }
  }

  // 撤销权限
  const handleRevokePermission = async (userId: string, username: string) => {
    if (!selectedNode) return
    
    Modal.confirm({
      title: '撤销权限',
      content: `确定要撤销用户 "${username}" 对此节点的访问权限吗？`,
      okText: '撤销',
      okType: 'danger',
      cancelText: '取消',
      onOk: async () => {
        try {
          await nodeService.revokeNodeFromUser(selectedNode.id, userId)
          showNotification('success', '权限已撤销')
          
          // 刷新用户列表
          const users = await nodeService.getNodeUsers(selectedNode.id)
          setNodeUsers(users)
        } catch (error: unknown) {
          const err = error as { message?: string }
          showNotification('error', err.message || '撤销权限失败')
        }
      }
    })
  }

  // 获取未分配权限的用户
  const availableUsers = useMemo(() => {
    const assignedUserIds = new Set(nodeUsers.map(u => String(u.user_id)))
    return allUsers.filter(u => !assignedUserIds.has(String(u.id)))
  }, [allUsers, nodeUsers])

  // 表格列
  const columns = [
    {
      title: '节点名称',
      dataIndex: 'name',
      key: 'name',
      width: 180,
      render: (name: string, record: Node) => (
        <Space>
          <Badge status={statusConfig[record.status].badge} />
          <Button type="link" onClick={() => openDetailModal(record)} style={{ padding: 0 }}>
            {name}
          </Button>
        </Space>
      )
    },
    {
      title: '地址',
      key: 'address',
      width: 180,
      render: (_: unknown, record: Node) => (
        <CopyableTooltip text={`${record.host}:${record.port}`}>
          <code style={{ cursor: 'pointer' }}>
            {record.host}:{record.port}
          </code>
        </CopyableTooltip>
      )
    },
    {
      title: '状态',
      dataIndex: 'status',
      key: 'status',
      width: 100,
      render: (status: NodeStatus) => (
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
      render: (_: unknown, record: Node) => {
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
      render: (_: unknown, record: Node) => (
        record.metrics?.cpu !== undefined ? (
          <Progress 
            percent={record.metrics.cpu} 
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
      render: (_: unknown, record: Node) => (
        record.metrics?.memory !== undefined ? (
          <Progress 
            percent={record.metrics.memory} 
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
      render: (_: unknown, record: Node) => (
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
      render: (_: unknown, record: Node) => {
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
      render: (_: unknown, record: Node) => (
        <Space size="small">
          <Tooltip title="进入节点" placement="top">
            <Button
              type="link"
              size="small"
              icon={<PlayCircleOutlined />}
              onClick={() => handleEnterNode(record)}
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
          <Tooltip title="重新绑定机器码" placement="top">
            <Button
              type="link"
              size="small"
              icon={<SwapOutlined />}
              onClick={() => openRebindModal(record)}
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
            title="确定删除此节点？"
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
  ]

  return (
    <div style={{ padding: '24px' }}>
      {/* 页面标题 */}
      <div style={{ marginBottom: '24px' }}>
        <h1 style={{ fontSize: '24px', fontWeight: 'bold', margin: 0, display: 'flex', alignItems: 'center', gap: '8px' }}>
          <ClusterOutlined />
          节点管理
        </h1>
        <p style={{ margin: '8px 0 0 0', opacity: 0.65 }}>
          管理和监控分布式工作节点
        </p>
      </div>

      {/* 统计卡片 */}
      <Row gutter={16} style={{ marginBottom: 16 }}>
        <Col xs={12} sm={8} md={6} lg={4}>
          <Card size="small">
            <Statistic 
              title="总节点" 
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
              onClick={() => refreshNodes()}
              loading={loading}
              size="small"
            >
              刷新
            </Button>
            <Button
              type="primary"
              icon={<LinkOutlined />}
              onClick={() => {
                connectForm.resetFields()
                setConnectModalVisible(true)
              }}
            >
              连接节点
            </Button>
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
              placeholder="搜索节点"
              allowClear
              style={{ width: 200 }}
              value={searchQuery}
              onChange={e => setSearchQuery(e.target.value)}
            />
          </Space>
        </div>
      </Card>

      {/* 节点表格 */}
      <Card>
        <ResponsiveTable
          dataSource={paginatedNodes}
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
            total: filteredNodes.length,
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

      {/* 连接节点弹窗 */}
      <Modal
        title={
          <Space>
            <LinkOutlined />
            连接节点
          </Space>
        }
        open={connectModalVisible}
        onCancel={() => {
          setConnectModalVisible(false)
          connectForm.resetFields()
        }}
        footer={null}
        width={500}
        forceRender
      >
        <Alert
          message="连接说明"
          description={
            <div>
              <p>1. 首先在目标机器上启动工作节点</p>
              <p>2. 节点启动后会显示<strong>机器码</strong></p>
              <p>3. 在下方输入节点地址、端口和机器码即可连接</p>
            </div>
          }
          type="info"
          showIcon
          style={{ marginBottom: 16 }}
        />
        <Form
          form={connectForm}
          layout="vertical"
          onFinish={handleConnectNode}
        >
          <Row gutter={16}>
            <Col span={16}>
              <Form.Item
                name="host"
                label="节点地址"
                rules={[{ required: true, message: '请输入节点地址' }]}
              >
                <Input placeholder="例如：192.168.1.100" />
              </Form.Item>
            </Col>
            <Col span={8}>
              <Form.Item
                name="port"
                label="端口"
                rules={[{ required: true, message: '请输入端口' }]}
                initialValue={8001}
              >
                <Input type="number" placeholder="8001" />
              </Form.Item>
            </Col>
          </Row>
          <Form.Item
            name="machine_code"
            label="机器码"
            rules={[{ required: true, message: '请输入机器码' }]}
            extra="机器码在节点启动时显示，用于验证节点身份"
          >
            <Input 
              placeholder="例如：DD4929696294EEFA" 
              style={{ fontFamily: 'monospace', letterSpacing: '2px' }}
            />
          </Form.Item>
          <Form.Item>
            <Space>
              <Button type="primary" htmlType="submit" loading={connecting}>
                连接
              </Button>
              <Button onClick={() => setConnectModalVisible(false)}>
                取消
              </Button>
            </Space>
          </Form.Item>
        </Form>
      </Modal>

      {/* 编辑弹窗 */}
      <Modal
        title="编辑节点"
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
                label="节点名称"
                rules={[{ required: true, message: '请输入节点名称' }]}
              >
                <Input placeholder="例如：Node-001" />
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
          <Row gutter={16}>
            <Col span={16}>
              <Form.Item
                name="host"
                label="主机地址"
                rules={[{ required: true, message: '请输入主机地址' }]}
              >
                <Input placeholder="例如：192.168.1.100 或 node1.example.com" />
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
          <Form.Item
            name="description"
            label="描述"
          >
            <Input.TextArea rows={3} placeholder="节点描述（可选）" />
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

      {/* 节点详情弹窗 */}
      <Modal
        title={
          <Space>
            <ClusterOutlined />
            节点详情
            {selectedNode && <Tag color="blue">{selectedNode.name}</Tag>}
          </Space>
        }
        open={detailModalVisible}
        onCancel={() => setDetailModalVisible(false)}
        footer={
          <Space>
            <Button onClick={() => setDetailModalVisible(false)}>关闭</Button>
            {selectedNode?.status === 'online' && (
              <Button type="primary" onClick={() => {
                if (selectedNode) handleEnterNode(selectedNode)
                setDetailModalVisible(false)
              }}>
                进入节点
              </Button>
            )}
          </Space>
        }
        width={900}
      >
        {selectedNode && (
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
            <Descriptions.Item label="节点名称">{selectedNode.name}</Descriptions.Item>
            <Descriptions.Item label="状态">
              <Tag color={statusConfig[selectedNode.status].color}>
                {statusConfig[selectedNode.status].text}
              </Tag>
            </Descriptions.Item>
            <Descriptions.Item label="地址">
              <Text code>{selectedNode.host}:{selectedNode.port}</Text>
            </Descriptions.Item>
            <Descriptions.Item label="区域">{selectedNode.region || '-'}</Descriptions.Item>
            
            {/* 操作系统信息 */}
            <Descriptions.Item label="操作系统">
              {selectedNode.osType ? (
                <Space>
                  {(() => {
                    const osInfo = getOsInfo(selectedNode.osType)
                    return (
                      <Tag 
                        color={osInfo.color}
                        style={{ display: 'inline-flex', alignItems: 'center', gap: '4px' }}
                      >
                        {React.cloneElement(osInfo.icon, { style: { fontSize: 12 } })}
                        <span>{osInfo.name}</span>
                      </Tag>
                    )
                  })()}
                  {selectedNode.osVersion && <Text type="secondary">{selectedNode.osVersion}</Text>}
                </Space>
              ) : '-'}
            </Descriptions.Item>
            <Descriptions.Item label="CPU 架构">
              {selectedNode.machineArch ? (
                <Tag color="cyan">{selectedNode.machineArch}</Tag>
              ) : '-'}
            </Descriptions.Item>
            <Descriptions.Item label="Python 版本">
              {selectedNode.pythonVersion ? (
                <Tag color="green">Python {selectedNode.pythonVersion}</Tag>
              ) : '-'}
            </Descriptions.Item>
            <Descriptions.Item label="节点版本">{selectedNode.version || '-'}</Descriptions.Item>
            
            {/* 渲染能力 */}
            <Descriptions.Item label="渲染能力" span={2}>
              {selectedNode.capabilities?.drissionpage?.enabled ? (
                <Tag color="green">有</Tag>
              ) : (
                <Tag color="default">无</Tag>
              )}
            </Descriptions.Item>
            
            <Descriptions.Item label="描述" span={2}>{selectedNode.description || '-'}</Descriptions.Item>
            <Descriptions.Item label="最后心跳" span={2}>
              {selectedNode.lastHeartbeat ? formatDateTime(selectedNode.lastHeartbeat) : '-'}
            </Descriptions.Item>
            {selectedNode.metrics && (
              <>
                <Descriptions.Item label="CPU 使用率">
                  <Tooltip 
                    title={
                      <div>
                        <div>使用率: {selectedNode.metrics.cpu.toFixed(1)}%</div>
                        {selectedNode.metrics.cpuCores && (
                          <div>核心数: {selectedNode.metrics.cpuCores} 核</div>
                        )}
                      </div>
                    }
                  >
                    <div style={{ cursor: 'pointer' }}>
                      <Progress percent={selectedNode.metrics.cpu} size="small" />
                    </div>
                  </Tooltip>
                </Descriptions.Item>
                <Descriptions.Item label="内存使用率">
                  <Tooltip 
                    title={
                      <div>
                        <div>使用率: {selectedNode.metrics.memory.toFixed(1)}%</div>
                        {selectedNode.metrics.memoryTotal && (
                          <>
                            <div>总内存: {(selectedNode.metrics.memoryTotal / 1024 / 1024 / 1024).toFixed(2)} GB</div>
                            <div>已使用: {((selectedNode.metrics.memoryUsed || 0) / 1024 / 1024 / 1024).toFixed(2)} GB</div>
                            <div>可用: {((selectedNode.metrics.memoryAvailable || 0) / 1024 / 1024 / 1024).toFixed(2)} GB</div>
                          </>
                        )}
                      </div>
                    }
                  >
                    <div style={{ cursor: 'pointer' }}>
                      <Progress percent={selectedNode.metrics.memory} size="small" />
                    </div>
                  </Tooltip>
                </Descriptions.Item>
                <Descriptions.Item label="磁盘使用率">
                  <Tooltip 
                    title={
                      <div>
                        <div>使用率: {selectedNode.metrics.disk.toFixed(1)}%</div>
                        {selectedNode.metrics.diskTotal && (
                          <>
                            <div>总容量: {(selectedNode.metrics.diskTotal / 1024 / 1024 / 1024).toFixed(2)} GB</div>
                            <div>已使用: {((selectedNode.metrics.diskUsed || 0) / 1024 / 1024 / 1024).toFixed(2)} GB</div>
                            <div>可用: {((selectedNode.metrics.diskFree || 0) / 1024 / 1024 / 1024).toFixed(2)} GB</div>
                          </>
                        )}
                      </div>
                    }
                  >
                    <div style={{ cursor: 'pointer' }}>
                      <Progress percent={selectedNode.metrics.disk} size="small" />
                    </div>
                  </Tooltip>
                </Descriptions.Item>
                <Descriptions.Item label="运行时间">
                  {Math.floor((selectedNode.metrics.uptime || 0) / 3600)} 小时
                </Descriptions.Item>
                <Descriptions.Item label="项目数">{selectedNode.metrics.projectCount}</Descriptions.Item>
                <Descriptions.Item label="环境数">{selectedNode.metrics.envCount}</Descriptions.Item>
                <Descriptions.Item label="总任务">{selectedNode.metrics.taskCount}</Descriptions.Item>
                <Descriptions.Item label="运行中任务">{selectedNode.metrics.runningTasks}</Descriptions.Item>
              </>
            )}
            <Descriptions.Item label="创建时间" span={2}>
              {formatDateTime(selectedNode.createdAt)}
            </Descriptions.Item>
          </Descriptions>
                )
              },
              {
                key: 'env',
                label: '环境管理',
                children: selectedNode.status === 'online' ? (
                  <NodeEnvManagement nodeId={selectedNode.id} nodeName={selectedNode.name} />
                ) : (
                  <Alert
                    message="节点离线"
                    description="节点当前处于离线状态，无法管理环境。请确保节点在线后再试。"
                    type="warning"
                    showIcon
                  />
                )
              },
              {
                key: 'resources',
                label: '资源管理',
                children: selectedNode.status === 'online' ? (
                  <NodeResourceManagement nodeId={selectedNode.id} nodeName={selectedNode.name} />
                ) : (
                  <Alert
                    message="节点离线"
                    description="节点当前处于离线状态，无法管理资源配置。请确保节点在线后再试。"
                    type="warning"
                    showIcon
                  />
                )
              }
            ]}
          />
        )}
      </Modal>

      {/* 权限管理弹窗 */}
      <Modal
        title={
          <Space>
            <TeamOutlined />
            节点权限管理
            {selectedNode && <Tag color="blue">{selectedNode.name}</Tag>}
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
        <Card size="small" title={`已授权用户 (${nodeUsers.length})`}>
          {loadingUsers ? (
            <div style={{ textAlign: 'center', padding: 20 }}>加载中...</div>
          ) : nodeUsers.length === 0 ? (
            <div style={{ textAlign: 'center', padding: 20, color: token.colorTextTertiary }}>
              暂无授权用户
            </div>
          ) : (
            <div style={{ maxHeight: 300, overflow: 'auto' }}>
              {nodeUsers.map(user => (
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

      {/* 重新绑定机器码弹窗 */}
      <Modal
        title={
          <Space>
            <SwapOutlined />
            重新绑定机器码
          </Space>
        }
        open={rebindModalVisible}
        onOk={handleRebind}
        onCancel={() => setRebindModalVisible(false)}
        confirmLoading={rebinding}
        okText="确认绑定"
        cancelText="取消"
        width={500}
        forceRender
      >
        <Alert
          message="使用说明"
          description={
            <ul style={{ marginBottom: 0, paddingLeft: 20 }}>
              <li>当节点重启后机器码变化时使用此功能</li>
              <li>从节点启动日志中获取新的机器码</li>
              <li>建议开启连接验证以确保机器码正确</li>
            </ul>
          }
          type="info"
          showIcon
          style={{ marginBottom: 16 }}
        />
        
        {selectedNode && (
          <Descriptions column={1} bordered size="small" style={{ marginBottom: 16 }}>
            <Descriptions.Item label="节点名称">{selectedNode.name}</Descriptions.Item>
            <Descriptions.Item label="节点地址">{selectedNode.host}:{selectedNode.port}</Descriptions.Item>
            <Descriptions.Item label="当前状态">
              <Tag color={selectedNode.status === 'online' ? 'success' : 'error'}>
                {selectedNode.status === 'online' ? '在线' : '离线'}
              </Tag>
            </Descriptions.Item>
          </Descriptions>
        )}
        
        <Form form={rebindForm} layout="vertical">
          <Form.Item
            name="new_machine_code"
            label="新机器码"
            rules={[
              { required: true, message: '请输入新的机器码' },
              { min: 1, max: 32, message: '机器码长度应为1-32位' }
            ]}
          >
            <Input 
              placeholder="从节点启动日志中复制新的机器码" 
              style={{ fontFamily: 'monospace' }}
            />
          </Form.Item>
          
          <Form.Item
            name="verify_connection"
            valuePropName="checked"
            initialValue={true}
          >
            <Checkbox>验证连接（推荐开启，确保机器码与节点匹配）</Checkbox>
          </Form.Item>
        </Form>
      </Modal>
    </div>
  )
}

export default Nodes

