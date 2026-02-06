import React, { useState, memo } from 'react'
import {
  Card,
  Button,
  Space,
  Tag,
  Input,
  Select,
  Tooltip,
  Popconfirm,
  Alert,
  Modal
} from 'antd'
import showNotification from '@/utils/notification'
import ResponsiveTable from '@/components/common/ResponsiveTable'
import {
  PlusOutlined,
  PlayCircleOutlined,
  DeleteOutlined,
  EditOutlined,
  EyeOutlined,
  ReloadOutlined,
  ScheduleOutlined,
  CloudServerOutlined,
  DesktopOutlined
} from '@ant-design/icons'
import { useNavigate } from 'react-router-dom'
import { useWorkerStore } from '@/stores/workerStore'
import type { Task, TaskStatus, ScheduleType } from '@/types'
import { formatDateTime } from '@/utils/format'
import useAuth from '@/hooks/useAuth'
import { useProjectsQuery, useTasksQuery, useTaskMutations } from '@/hooks/api/useTasks'

const { Search } = Input
const { Option } = Select

const Tasks: React.FC = memo(() => {
  const navigate = useNavigate()
  const { isAuthenticated, loading: authLoading } = useAuth()
  const { currentWorker } = useWorkerStore()

  const [searchQuery, setSearchQuery] = useState('')
  const [projectFilter, setProjectFilter] = useState<string | undefined>(undefined)
  const [statusFilter, setStatusFilter] = useState<TaskStatus | undefined>(undefined)
  const [scheduleTypeFilter, setScheduleTypeFilter] = useState<ScheduleType | undefined>(undefined)
  const [currentPage, setCurrentPage] = useState(1)
  const [pageSize, setPageSize] = useState(10)
  const [selectedRowKeys, setSelectedRowKeys] = useState<React.Key[]>([])

  const tasksQuery = useTasksQuery({
    page: currentPage,
    size: pageSize,
    project_id: projectFilter,
    status: statusFilter,
    schedule_type: scheduleTypeFilter,
    search: searchQuery?.trim() || undefined,
    specified_worker_id: currentWorker?.id
  }, isAuthenticated && !authLoading)

  const projectsQuery = useProjectsQuery(isAuthenticated && !authLoading)
  const { triggerTask, deleteTask, batchDelete } = useTaskMutations()

  const loading = tasksQuery.isLoading || tasksQuery.isFetching
  const tasks = tasksQuery.data?.items || []
  const total = tasksQuery.data?.total || 0
  const projects = projectsQuery.data?.items || []
  const requestError = tasksQuery.error as Error | null

  const handleRefresh = () => {
    tasksQuery.refetch()
  }

  // 处理筛选变化时重置到第一页
  const handleSearchChange = (value: string) => {
    setSearchQuery(value)
    setCurrentPage(1)
  }

  // 实时搜索（输入时立即筛选）
  const handleSearchInput = (e: React.ChangeEvent<HTMLInputElement>) => {
    setSearchQuery(e.target.value)
    setCurrentPage(1)
  }

  const handleProjectFilterChange = (value: string | undefined) => {
    setProjectFilter(value)
    setCurrentPage(1)
  }

  const handleStatusFilterChange = (value: TaskStatus | undefined) => {
    setStatusFilter(value)
    setCurrentPage(1)
  }

  const handleScheduleTypeFilterChange = (value: ScheduleType | undefined) => {
    setScheduleTypeFilter(value)
    setCurrentPage(1)
  }

  // 处理分页变化
  const handlePaginationChange = (page: number, size: number) => {
    setCurrentPage(page)
    if (size !== pageSize) {
      setPageSize(size)
      setCurrentPage(1)
    }
  }

  // 认证加载状态
  if (authLoading) {
    return (
      <div style={{ padding: '24px', textAlign: 'center' }}>
        <div style={{ marginTop: '100px' }}>
          <div style={{ fontSize: '16px', marginBottom: '16px' }}>
            正在验证登录状态...
          </div>
        </div>
      </div>
    )
  }

  // 未认证状态
  if (!isAuthenticated) {
    return (
      <div style={{ padding: '24px' }}>
        <Alert
          message="需要登录"
          description="请先登录后再访问任务管理页面"
          type="warning"
          showIcon
          action={
            <Button type="primary" onClick={() => navigate('/login')}>
              去登录
            </Button>
          }
        />
      </div>
    )
  }

  // 认证错误状态
  if (requestError) {
    const description = requestError?.message || '加载失败，请稍后重试'
    return (
      <div style={{ padding: '24px' }}>
        <Alert
          message="加载失败"
          description={description}
          type="error"
          showIcon
          action={
            <Space>
              <Button onClick={() => window.location.reload()}>
                刷新页面
              </Button>
              <Button type="primary" onClick={() => navigate('/login')}>
                重新登录
              </Button>
            </Space>
          }
        />
      </div>
    )
  }

  // 触发任务
  const handleTriggerTask = async (taskId: string) => {
    try {
      const resp = await triggerTask.mutateAsync(taskId)
      if (resp?.message) {
        showNotification('success', resp.message)
      } else {
        showNotification('success', '任务已触发')
      }
    } catch (_error: unknown) {
      // 错误提示由全局拦截器统一处理
    }
  }

  // 删除任务
  const handleDeleteTask = async (taskId: string) => {
    try {
      await deleteTask.mutateAsync(taskId)
      showNotification('success', '任务已删除')
      setSelectedRowKeys((prev) => prev.filter(key => String(key) !== taskId))
    } catch (_error: unknown) {
      // 通知由拦截器统一处理
    }
  }

  // 批量删除任务
  const handleBatchDelete = () => {
    if (selectedRowKeys.length === 0) {
      showNotification('warning', '请先选择要删除的任务')
      return
    }

    Modal.confirm({
      title: '确认批量删除',
      content: `确定要删除选中的 ${selectedRowKeys.length} 个任务吗？此操作不可恢复。`,
      okText: '确认删除',
      okType: 'danger',
      cancelText: '取消',
      onOk: async () => {
        try {
          const result = await batchDelete.mutateAsync(selectedRowKeys.map(String))
          if (result.success_count > 0) {
            showNotification('success', `成功删除 ${result.success_count} 个任务`)
            setSelectedRowKeys([])
          }
          if (result.failed_count > 0) {
            showNotification('warning', `${result.failed_count} 个任务删除失败`)
          }
        } catch (_error: unknown) {
          showNotification('error', '批量删除失败，请稍后重试')
        }
      }
    })
  }

  // 获取任务状态标签
  const getStatusTag = (status: TaskStatus) => {
    const statusMap: Record<string, { color: string; text: string; icon?: React.ReactNode }> = {
      pending: { color: 'default', text: '等待调度' },
      dispatching: { color: 'processing', text: '分配 Worker 中' },
      queued: { color: 'cyan', text: '排队中' },
      running: { color: 'processing', text: '执行中' },
      success: { color: 'success', text: '成功' },
      failed: { color: 'error', text: '失败' },
      cancelled: { color: 'warning', text: '已取消' },
      timeout: { color: 'error', text: '超时' },
      paused: { color: 'warning', text: '已暂停' },
      skipped: { color: 'default', text: '已跳过' }
    }
    const config = statusMap[status] || { color: 'default', text: status }
    return <Tag color={config.color}>{config.text}</Tag>
  }

  // 获取调度类型标签
  const getScheduleTypeTag = (type: ScheduleType) => {
    const typeMap = {
      once: { color: 'blue', text: '一次性' },
      interval: { color: 'green', text: '间隔执行' },
      cron: { color: 'purple', text: 'Cron' },
      date: { color: 'orange', text: '指定时间' }
    }
    const config = typeMap[type] || { color: 'default', text: type }
    return <Tag color={config.color}>{config.text}</Tag>
  }

  return (
    <div style={{ padding: '24px' }}>
      <div style={{ marginBottom: '24px' }}>
        <Space align="start">
          <h1 style={{ fontSize: '24px', fontWeight: 'bold', margin: 0, display: 'flex', alignItems: 'center', gap: '8px' }}>
            <ScheduleOutlined />
            任务管理
          </h1>
          {currentWorker && (
            <Tag 
              color="cyan"
              style={{ display: 'inline-flex', alignItems: 'center', gap: '4px', marginTop: '4px' }}
            >
              <CloudServerOutlined style={{ fontSize: 12 }} />
              <span>{currentWorker.name}</span>
            </Tag>
          )}
        </Space>
        <p style={{ margin: '8px 0 0 0', opacity: 0.65 }}>
          {currentWorker ? `当前 Worker: ${currentWorker.name}` : '管理和监控您的调度任务'}
        </p>
      </div>

      {/* 操作栏 */}
      <Card style={{ marginBottom: 16 }}>
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', flexWrap: 'wrap', gap: '12px' }}>
          <Space wrap size="middle">
            <Button
              icon={<ReloadOutlined />}
              onClick={handleRefresh}
              loading={loading}
            >
              刷新
            </Button>
            <Button
              type="primary"
              icon={<PlusOutlined />}
              onClick={() => navigate('/tasks/create')}
            >
              创建任务
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
              placeholder="项目"
              allowClear
              style={{ width: 120 }}
              value={projectFilter}
              onChange={handleProjectFilterChange}
            >
              {projects.map(project => (
                <Option key={project.id} value={project.id}>
                  {project.name}
                </Option>
              ))}
            </Select>
            <Select
              placeholder="状态"
              allowClear
              style={{ width: 100 }}
              value={statusFilter}
              onChange={handleStatusFilterChange}
            >
              <Option value="pending">等待中</Option>
              <Option value="running">运行中</Option>
              <Option value="success">成功</Option>
              <Option value="failed">失败</Option>
              <Option value="cancelled">已取消</Option>
            </Select>
            <Select
              placeholder="调度类型"
              allowClear
              style={{ width: 110 }}
              value={scheduleTypeFilter}
              onChange={handleScheduleTypeFilterChange}
            >
              <Option value="once">一次性</Option>
              <Option value="date">指定时间</Option>
              <Option value="interval">间隔执行</Option>
              <Option value="cron">Cron</Option>
            </Select>
            <Search
              placeholder="搜索任务"
              allowClear
              style={{ width: 200 }}
              value={searchQuery}
              onChange={handleSearchInput}
              onSearch={handleSearchChange}
            />
          </Space>
        </div>
      </Card>

      {/* 任务表格 */}
      <Card>
        <ResponsiveTable
          dataSource={tasks}
          loading={loading}
          minWidth={900}
          fixedActions={true}
          rowSelection={{
            selectedRowKeys,
            onChange: (keys) => setSelectedRowKeys(keys),
            preserveSelectedRowKeys: true
          }}
          pagination={{
            current: currentPage,
            pageSize: pageSize,
            total: total,
            showSizeChanger: true,
            showQuickJumper: true,
            showTotal: (total, range) => `第 ${range[0]}-${range[1]} 条，共 ${total} 条`,
            onChange: handlePaginationChange,
            onShowSizeChange: (_, size) => handlePaginationChange(1, size),
            pageSizeOptions: ['10', '20', '50', '100']
          }}
          rowKey="id"
          size="middle"
          columns={[
            {
              title: '任务名称',
              dataIndex: 'name',
              key: 'name',
              width: 200,
              ellipsis: { showTitle: false },
              render: (text: string, record: Task) => (
                <Tooltip title={text} placement="topLeft">
                  <Button
                    type="link"
                    onClick={() => navigate(`/tasks/${record.id}`)}
                    style={{
                      padding: 0,
                      height: 'auto',
                      textAlign: 'left',
                      overflow: 'hidden',
                      textOverflow: 'ellipsis',
                      whiteSpace: 'nowrap',
                      maxWidth: '100%',
                      display: 'block'
                    }}
                  >
                    {text}
                  </Button>
                </Tooltip>
              )
            },
            {
              title: '状态',
              dataIndex: 'status',
              key: 'status',
              width: 100,
              render: (status: TaskStatus) => getStatusTag(status)
            },
            {
              title: '调度类型',
              dataIndex: 'schedule_type',
              key: 'schedule_type',
              width: 120,
              responsive: ['md'],
              render: (type: ScheduleType) => getScheduleTypeTag(type)
            },
            {
              title: '执行 Worker',
              key: 'worker',
              width: 130,
              responsive: ['lg'],
              render: (_: string, record: Task) => {
                const strategy = record.execution_strategy || record.project_execution_strategy

                let workerName = '本地'
                let icon = <DesktopOutlined style={{ fontSize: 12 }} />
                let color: string = 'geekblue'

                if (strategy === 'auto') {
                  workerName = '自动选择'
                  icon = <CloudServerOutlined style={{ fontSize: 12 }} />
                  color = 'green'
                } else if (strategy === 'specified') {
                  workerName = record.specified_worker_name || record.specified_worker_id || '指定 Worker'
                  icon = <CloudServerOutlined style={{ fontSize: 12 }} />
                  color = 'cyan'
                } else if (strategy === 'fixed' || strategy === 'prefer') {
                  workerName = record.project_bound_worker_name || record.project_bound_worker_id || '绑定 Worker'
                  icon = <CloudServerOutlined style={{ fontSize: 12 }} />
                  color = 'blue'
                }
                return (
                  <Tooltip title={workerName} placement="topLeft">
                    <Tag 
                      color={color}
                      style={{ 
                        maxWidth: '100%', 
                        overflow: 'hidden', 
                        textOverflow: 'ellipsis', 
                        display: 'inline-flex', 
                        alignItems: 'center', 
                        gap: '4px' 
                      }}
                    >
                      {icon}
                      <span style={{ overflow: 'hidden', textOverflow: 'ellipsis' }}>{workerName}</span>
                    </Tag>
                  </Tooltip>
                )
              }
            },
            {
              title: '是否启用',
              dataIndex: 'is_active',
              key: 'is_active',
              width: 100,
              responsive: ['lg'],
              render: (isActive: boolean) => (
                <Tag color={isActive ? 'success' : 'default'}>
                  {isActive ? '启用' : '禁用'}
                </Tag>
              )
            },
            {
              title: '创建者',
              dataIndex: 'created_by_username',
              key: 'created_by_username',
              width: 120,
              ellipsis: { showTitle: false },
              responsive: ['lg'],
              render: (username: string) => (
                <Tooltip title={username || '未知用户'} placement="topLeft">
                  <span>{username || '未知用户'}</span>
                </Tooltip>
              )
            },
            {
              title: '创建时间',
              dataIndex: 'created_at',
              key: 'created_at',
              width: 180,
              ellipsis: { showTitle: false },
              responsive: ['xl'],
              render: (time: string) => (
                <Tooltip title={formatDateTime(time)} placement="topLeft">
                  <span>{formatDateTime(time)}</span>
                </Tooltip>
              )
            },
            {
              title: '操作',
              key: 'actions',
              width: 160,
              minWidth: 120,
              fixed: 'right',
              render: (_, record: Task) => (
                <div className="table-actions">
                  <Space size="small" wrap>
                    <Tooltip title="执行" placement="top">
                      <Button
                        type="text"
                        size="small"
                        icon={<PlayCircleOutlined />}
                        onClick={() => handleTriggerTask(record.id)}
                        className="action-btn"
                      />
                    </Tooltip>
                    <Tooltip title="查看" placement="top">
                      <Button
                        type="text"
                        size="small"
                        icon={<EyeOutlined />}
                        onClick={() => navigate(`/tasks/${record.id}`)}
                        className="action-btn"
                      />
                    </Tooltip>
                    <Tooltip title="编辑" placement="top">
                      <Button
                        type="text"
                        size="small"
                        icon={<EditOutlined />}
                        onClick={() => navigate(`/tasks/${record.id}/edit`)}
                        className="action-btn hidden-sm"
                      />
                    </Tooltip>
                    <Popconfirm
                      title="确定要删除这个任务吗？"
                      onConfirm={() => handleDeleteTask(record.id)}
                      okText="确定"
                      cancelText="取消"
                    >
                      <Tooltip title="删除" placement="top">
                        <Button
                          type="text"
                          size="small"
                          icon={<DeleteOutlined />}
                          danger
                          className="action-btn"
                        />
                      </Tooltip>
                    </Popconfirm>
                  </Space>
                </div>
              )
            }
          ]}
        />
      </Card>
    </div>
  )
})

export default Tasks

Tasks.displayName = 'TasksPage'
