import React, { useState, useEffect, memo, useMemo } from 'react'
import {
  Card,
  Button,
  Space,
  Tag,
  Input,
  Select,
  Tooltip,
  Popconfirm,
  Alert
} from 'antd'
import showNotification from '@/utils/notification'
import ResponsiveTable from '@/components/common/ResponsiveTable'
import {
  PlusOutlined,
  PlayCircleOutlined,
  DeleteOutlined,
  EditOutlined,
  EyeOutlined,
  ReloadOutlined
} from '@ant-design/icons'
import { useNavigate } from 'react-router-dom'
import { taskService } from '@/services/tasks'
import { projectService } from '@/services/projects'
import type { Task, TaskStatus, ScheduleType, Project } from '@/types'
import { formatDateTime } from '@/utils/format'
import useAuth from '@/hooks/useAuth'

const { Search } = Input
const { Option } = Select

const Tasks: React.FC = memo(() => {
  const navigate = useNavigate()
  const { isAuthenticated, loading: authLoading } = useAuth()
  
  // 所有任务数据（从后端一次性加载）
  const [allTasks, setAllTasks] = useState<Task[]>([])
  const [projects, setProjects] = useState<Project[]>([])
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)

  // 前端筛选条件
  const [searchQuery, setSearchQuery] = useState('')
  const [projectFilter, setProjectFilter] = useState<number | undefined>(undefined)

  // 前端分页
  const [currentPage, setCurrentPage] = useState(1)
  const [pageSize, setPageSize] = useState(10)

  // 智能加载任务列表（根据总数判断加载策略）
  const loadAllTasks = async () => {
    if (!isAuthenticated) {
      setError('请先登录')
      return
    }

    setLoading(true)
    setError(null)
    try {
      // 先获取第一页，查看总数
      const firstPageResponse = await taskService.getTasks({ page: 1, size: 100 })
      const totalCount = firstPageResponse.total

      // 如果总数小于等于100，直接使用
      if (totalCount <= 100) {
        setAllTasks(firstPageResponse.items)
      } else {
        // 如果总数大于100，分批加载（最多加载前10页，即1000条数据）
        const allItems = [...firstPageResponse.items]
        const totalPages = Math.ceil(totalCount / 100)
        const pagesToLoad = Math.min(totalPages, 10)
        
        const promises = []
        for (let page = 2; page <= pagesToLoad; page++) {
          promises.push(taskService.getTasks({ page, size: 100 }))
        }
        
        const results = await Promise.all(promises)
        results.forEach(response => {
          allItems.push(...response.items)
        })
        
        setAllTasks(allItems)
      }
    } catch (error: unknown) {
      const axiosError = error as { response?: { status?: number }; message?: string }
      const errorMessage = axiosError.response?.status === 401
        ? '认证已过期，请重新登录' 
        : '加载任务列表失败: ' + (axiosError.message || '未知错误')
      setError(errorMessage)
      setAllTasks([])
    } finally {
      setLoading(false)
    }
  }

  // 加载项目列表
  const loadProjects = async () => {
    try {
      const response = await projectService.getProjects({ page: 1, size: 100 })
      setProjects(response.items || [])
    } catch {
      setProjects([])
    }
  }

  // 前端筛选和分页逻辑
  const filteredAndPaginatedTasks = useMemo(() => {
    let filtered = [...allTasks]

    // 应用搜索筛选
    if (searchQuery) {
      const lowerQuery = searchQuery.toLowerCase().trim()
      filtered = filtered.filter(task => {
        return (
          task.name?.toLowerCase().includes(lowerQuery) ||
          task.description?.toLowerCase().includes(lowerQuery)
        )
      })
    }

    // 应用项目筛选
    if (projectFilter) {
      filtered = filtered.filter(task => task.project_id === projectFilter)
    }

    // 计算分页
    const total = filtered.length
    const startIndex = (currentPage - 1) * pageSize
    const endIndex = startIndex + pageSize
    const paginatedData = filtered.slice(startIndex, endIndex)

    return {
      data: paginatedData,
      total: total
    }
  }, [allTasks, searchQuery, projectFilter, currentPage, pageSize])

  // 初始化数据 - 只在认证后加载
  useEffect(() => {
    if (isAuthenticated && !authLoading) {
      loadAllTasks()
      loadProjects()
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [isAuthenticated, authLoading])

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
  if (error) {
    return (
      <div style={{ padding: '24px' }}>
        <Alert
          message="加载失败"
          description={error}
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
  const handleTriggerTask = async (taskId: number) => {
    try {
      const resp = await taskService.triggerTask(taskId)
      if (resp?.message) {
        showNotification('success', resp.message)
      } else {
        showNotification('success', '任务已触发')
      }
      loadAllTasks() // 重新加载任务列表
    } catch {
      // 错误由拦截器处理
    }
  }

  // 删除任务
  const handleDeleteTask = async (taskId: number) => {
    try {
      await taskService.deleteTask(taskId)
      
      // 从allTasks中移除
      setAllTasks(prev => prev.filter(t => t.id !== taskId))

      // 检查当前页是否还有其他数据
      const remainingTasks = filteredAndPaginatedTasks.data.filter(t => t.id !== taskId)
      if (remainingTasks.length === 0 && currentPage > 1) {
        setCurrentPage(currentPage - 1)
      }
    } catch {
      // 通知由拦截器统一处理
    }
  }

  // 获取任务状态标签
  const getStatusTag = (status: TaskStatus) => {
    const statusMap: Record<string, { color: string; text: string }> = {
      pending: { color: 'default', text: '等待调度' },
      dispatching: { color: 'processing', text: '分配节点中' },
      queued: { color: 'cyan', text: '排队中' },
      running: { color: 'processing', text: '执行中' },
      success: { color: 'success', text: '成功' },
      completed: { color: 'success', text: '已完成' },
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
      cron: { color: 'purple', text: 'Cron' }
    }
    const config = typeMap[type] || { color: 'default', text: type }
    return <Tag color={config.color}>{config.text}</Tag>
  }

  // 处理筛选变化时重置到第一页
  const handleSearchChange = (value: string) => {
    setSearchQuery(value)
    setCurrentPage(1)
  }

  const handleProjectChange = (value: number | undefined) => {
    setProjectFilter(value)
    setCurrentPage(1)
  }

  // 处理分页变化
  const handlePaginationChange = (page: number, size: number) => {
    setCurrentPage(page)
    if (size !== pageSize) {
      setPageSize(size)
      setCurrentPage(1) // 改变页大小时重置到第一页
    }
  }

  return (
    <div style={{ padding: '24px' }}>
      <div style={{ marginBottom: '24px' }}>
        <h1 style={{ fontSize: '24px', fontWeight: 'bold', margin: 0 }}>
          任务管理
        </h1>
        <p style={{ margin: '8px 0 0 0', opacity: 0.65 }}>
          管理和监控您的调度任务
        </p>
      </div>

      {/* 操作栏 */}
      <Card style={{ marginBottom: 16 }}>
        <div className="toolbar-container">
          {/* 主要操作按钮 */}
          <div className="toolbar-actions">
            <Space wrap>
              <Button
                icon={<ReloadOutlined />}
                onClick={() => loadAllTasks()}
                loading={loading}
                size="middle"
              >
                <span className="hidden-xs">刷新</span>
              </Button>
              <Button
                type="primary"
                icon={<PlusOutlined />}
                onClick={() => navigate('/tasks/create')}
                size="middle"
              >
                <span className="hidden-xs">创建任务</span>
              </Button>
            </Space>
          </div>

          {/* 筛选和搜索 */}
          <div className="toolbar-filters">
            <Space wrap>
              <Search
                placeholder="搜索任务"
                allowClear
                style={{ width: 200, minWidth: 150 }}
                onSearch={handleSearchChange}
                value={searchQuery}
                onChange={(e) => setSearchQuery(e.target.value)}
                size="middle"
              />
              <Select
                placeholder="项目"
                allowClear
                style={{ width: 120, minWidth: 100 }}
                value={projectFilter}
                onChange={handleProjectChange}
                size="middle"
              >
                {projects.map(project => (
                  <Option key={project.id} value={project.id}>
                    {project.name}
                  </Option>
                ))}
              </Select>
            </Space>
          </div>
        </div>
      </Card>

      {/* 任务表格 */}
      <Card>
        <ResponsiveTable
          dataSource={filteredAndPaginatedTasks.data}
          loading={loading}
          minWidth={900}
          fixedActions={true}
          pagination={{
            current: currentPage,
            pageSize: pageSize,
            total: filteredAndPaginatedTasks.total,
            showSizeChanger: true,
            showQuickJumper: true,
            showTotal: (total, range) => `第 ${range[0]}-${range[1]} 条，共 ${total} 条`,
            onChange: (page, size) => {
              handlePaginationChange(page, size || pageSize)
            },
            onShowSizeChange: (current, size) => {
              handlePaginationChange(1, size)
            }
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
                    <Tooltip title="执行">
                      <Button
                        type="text"
                        size="small"
                        icon={<PlayCircleOutlined />}
                        onClick={() => handleTriggerTask(record.id)}
                        className="action-btn"
                      />
                    </Tooltip>
                    <Tooltip title="查看">
                      <Button
                        type="text"
                        size="small"
                        icon={<EyeOutlined />}
                        onClick={() => navigate(`/tasks/${record.id}`)}
                        className="action-btn"
                      />
                    </Tooltip>
                    <Tooltip title="编辑">
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
                      <Tooltip title="删除">
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
