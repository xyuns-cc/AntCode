import type React from 'react'
import {
  useEffect,
  useReducer,
  useRef,
  useState,
  useCallback,
  Suspense,
  lazy,
  useMemo,
} from 'react'
import { App, Button, Space, Tag, Modal, Input, Select, Card, Tooltip } from 'antd'
import ResponsiveTable from '@/components/common/ResponsiveTable'
import {
  PlusOutlined,
  EditOutlined,
  DeleteOutlined,
  PlayCircleOutlined,
  EyeOutlined,
  ReloadOutlined,
  FolderOutlined,
  CloudServerOutlined,
} from '@ant-design/icons'
import { useNavigate } from 'react-router'
import { useProjects } from '@/stores/projectStore'
import { useWorkerStore } from '@/stores/workerStore'
import { projectService } from '@/services/projects'
import { describeBatchFailures } from '@/services/batchOutcome'
import { formatDate } from '@/utils/format'
import {
  getProjectTypeText,
  getProjectStatusText,
  getProjectTypeColor,
  getProjectStatusColor,
} from '@/utils/projectUtils'
import useAuth from '@/hooks/useAuth'
import { userService, type SimpleUser } from '@/services/users'
import { isAbortError } from '@/utils/helpers'
import Logger from '@/utils/logger'
import type { Project, ProjectListParams, ProjectType } from '@/types'
import type { ColumnsType } from 'antd/es/table'
import { initialUIState, uiReducer } from './projectListUiState'
import ProjectDeleteWarning from './ProjectDeleteWarning'

const { Search } = Input
const { Option } = Select
const BATCH_DELETE_MAX_PROJECTS = 100
const ProjectCreateDrawer = lazy(() => import('@/components/projects/ProjectCreateDrawer'))
const ProjectEditDrawer = lazy(() => import('@/components/projects/ProjectEditDrawer'))

const ProjectList: React.FC = () => {
  const { message } = App.useApp()
  const navigate = useNavigate()
  const { isAuthenticated, loading: authLoading, user } = useAuth()
  const { currentWorker } = useWorkerStore()
  const [uiState, dispatch] = useReducer(uiReducer, initialUIState)
  const currentUserIdRef = useRef<string | undefined>(user?.id)
  const mountedRef = useRef(true)
  const controllersRef = useRef<Record<string, AbortController | null>>({})
  const [userList, setUserList] = useState<SimpleUser[]>([])
  const [loadingUsers, setLoadingUsers] = useState(false)

  const [projects, setPageProjects] = useState<Project[]>([])
  const [projectTotal, setProjectTotal] = useState(0)

  const [searchInputValue, setSearchInputValue] = useState('')
  const [searchQuery, setSearchQuery] = useState('')
  const [typeFilter, setTypeFilter] = useState<ProjectListParams['type'] | undefined>(undefined)
  const [statusFilter, setStatusFilter] = useState<ProjectListParams['status'] | undefined>(
    undefined
  )
  const [createdByFilter, setCreatedByFilter] = useState<string | undefined>(undefined)

  // 服务端分页
  const [currentPage, setCurrentPage] = useState(1)
  const [pageSize, setPageSize] = useState(10)

  const { setProjects, setPagination, removeProject } = useProjects()

  const resolveSelectedProjects = useCallback(
    (selectedKeys: React.Key[], currentRows: Project[]) => {
      if (selectedKeys.length === 0) {
        return []
      }
      const selectedKeySet = new Set(selectedKeys.map((key) => String(key)))
      const retained = uiState.selectedProjects.filter((project) => selectedKeySet.has(project.id))
      const knownIds = new Set(retained.map((project) => project.id))
      return currentRows.reduce((selected, project) => {
        if (selectedKeySet.has(project.id) && !knownIds.has(project.id)) {
          selected.push(project)
          knownIds.add(project.id)
        }
        return selected
      }, retained)
    },
    [uiState.selectedProjects]
  )

  const createController = useCallback((key: string) => {
    const existing = controllersRef.current[key]
    if (existing) {
      existing.abort()
    }
    const controller = new AbortController()
    controllersRef.current[key] = controller
    return controller
  }, [])

  const isLatestController = useCallback((key: string, controller: AbortController) => {
    return controllersRef.current[key] === controller && !controller.signal.aborted
  }, [])

  useEffect(() => {
    const controllers = controllersRef
    mountedRef.current = true
    return () => {
      mountedRef.current = false
      Object.values(controllers.current).forEach((controller) => controller?.abort())
    }
  }, [])

  // 获取用户列表（仅管理员需要）
  const fetchUserList = useCallback(async () => {
    if (!user?.is_admin) return

    const controller = createController('users')
    setLoadingUsers(true)
    try {
      const users = await userService.getSimpleUserList({ signal: controller.signal })
      if (!mountedRef.current || !isLatestController('users', controller)) return
      setUserList(users)
    } catch (error) {
      if (isAbortError(error)) return
      Logger.error('加载用户列表失败:', error)
      message.error('加载用户列表失败')
    } finally {
      if (mountedRef.current && isLatestController('users', controller)) {
        setLoadingUsers(false)
      }
    }
  }, [user?.is_admin, createController, isLatestController, message])

  const fetchProjects = useCallback(async () => {
    if (!isAuthenticated || !user) return

    const controller = createController('projects')
    const requestConfig = { signal: controller.signal }

    dispatch({ type: 'SET_LOADING', payload: true })
    try {
      const params: ProjectListParams = {
        page: currentPage,
        size: pageSize,
        type: typeFilter,
        status: statusFilter,
        created_by: user.is_admin ? createdByFilter : undefined,
        search: searchQuery.trim() || undefined,
        worker_id: currentWorker?.id,
      }
      const response = await projectService.getProjects(params, requestConfig)

      if (!mountedRef.current || !isLatestController('projects', controller)) return
      const lastPage = Math.max(1, Math.ceil(response.total / pageSize))
      if (currentPage > lastPage) {
        setCurrentPage(lastPage)
        return
      }
      setPageProjects(response.items)
      setProjectTotal(response.total)
      setProjects(response.items)
    } catch (error) {
      if (isAbortError(error)) return
      if (!mountedRef.current || !isLatestController('projects', controller)) return
      Logger.error('加载项目列表失败:', error)
      message.error('加载项目列表失败')
    } finally {
      if (mountedRef.current && isLatestController('projects', controller)) {
        dispatch({ type: 'SET_LOADING', payload: false })
      }
    }
  }, [
    createController,
    createdByFilter,
    currentPage,
    currentWorker?.id,
    isAuthenticated,
    isLatestController,
    message,
    pageSize,
    searchQuery,
    setProjects,
    statusFilter,
    typeFilter,
    user,
  ])

  // 同步分页信息到 store（使用 useEffect 避免在渲染期间更新状态）
  useEffect(() => {
    setPagination({
      current: currentPage,
      pageSize: pageSize,
      total: projectTotal,
    })
  }, [currentPage, pageSize, projectTotal, setPagination])

  // 初始加载
  useEffect(() => {
    if (!isAuthenticated && !authLoading) {
      setPageProjects([])
      setProjectTotal(0)
      setProjects([])
    }
  }, [isAuthenticated, authLoading, setProjects])

  useEffect(() => {
    if (!user) {
      currentUserIdRef.current = undefined
      return
    }

    if (user.id !== currentUserIdRef.current) {
      currentUserIdRef.current = user.id
      // 用户切换时重置筛选条件
      setSearchInputValue('')
      setSearchQuery('')
      setTypeFilter(undefined)
      setStatusFilter(undefined)
      setCreatedByFilter(undefined)
      setCurrentPage(1)
    }
  }, [user])

  useEffect(() => {
    if (!isAuthenticated || authLoading || !user) {
      return
    }

    // 首次加载或 Worker 切换时重新加载
    fetchProjects()
  }, [isAuthenticated, authLoading, user, fetchProjects])

  // 获取用户列表（管理员专用）
  useEffect(() => {
    if (user?.is_admin && isAuthenticated && !authLoading) {
      fetchUserList()
    }
  }, [user?.is_admin, isAuthenticated, authLoading, fetchUserList])

  // 处理筛选变化时重置到第一页
  const handleSearchChange = (value: string) => {
    setSearchInputValue(value)
    setSearchQuery(value.trim())
    setCurrentPage(1)
  }

  const handleSearchInput = (e: React.ChangeEvent<HTMLInputElement>) => {
    const value = e.target.value
    setSearchInputValue(value)
    if (!value.trim()) {
      setSearchQuery('')
      setCurrentPage(1)
    }
  }

  const handleTypeChange = (value: ProjectListParams['type'] | undefined) => {
    setTypeFilter(value)
    setCurrentPage(1)
  }

  const handleStatusChange = (value: ProjectListParams['status'] | undefined) => {
    setStatusFilter(value)
    setCurrentPage(1)
  }

  const handleCreatedByChange = (value: string | undefined) => {
    setCreatedByFilter(value)
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

  const handleDelete = useCallback(
    (project: Project) => {
      dispatch({ type: 'SHOW_DELETE_MODAL', payload: project })
    },
    [dispatch]
  )

  // 编辑项目
  const handleEdit = useCallback(
    async (project: Project) => {
      try {
        dispatch({ type: 'SET_LOADING', payload: true })
        // 获取完整的项目详情
        const fullProject = await projectService.getProject(project.id)
        dispatch({ type: 'SET_CURRENT_EDIT_PROJECT', payload: fullProject })
        dispatch({ type: 'TOGGLE_EDIT_DRAWER', payload: true })
      } catch (error) {
        Logger.error('加载项目详情失败:', error)
        message.error('加载项目详情失败')
      } finally {
        dispatch({ type: 'SET_LOADING', payload: false })
      }
    },
    [dispatch, message]
  )

  // 确认删除单个项目
  const confirmDelete = async () => {
    if (!uiState.currentDeleteProject) return

    try {
      await projectService.deleteProject(uiState.currentDeleteProject.id)
      removeProject(uiState.currentDeleteProject.id)

      // 如果删除的项目在选中列表中，需要更新选中状态
      if (uiState.selectedRowKeys.includes(uiState.currentDeleteProject.id)) {
        dispatch({ type: 'REMOVE_SELECTED_PROJECT', payload: uiState.currentDeleteProject.id })
      }
      await fetchProjects()
    } catch (error) {
      Logger.error('删除项目失败:', error)
      message.error('删除项目失败')
    } finally {
      dispatch({ type: 'HIDE_DELETE_MODAL' })
    }
  }

  // 批量删除项目
  const handleBatchDelete = () => {
    if (uiState.selectedRowKeys.length === 0) {
      return
    }
    if (uiState.selectedRowKeys.length > BATCH_DELETE_MAX_PROJECTS) {
      message.error(`单次最多删除 ${BATCH_DELETE_MAX_PROJECTS} 个项目，请减少选择数量`)
      return
    }

    dispatch({ type: 'SHOW_BATCH_DELETE_MODAL' })
  }

  // 确认批量删除
  const confirmBatchDelete = async () => {
    const projectIds = uiState.selectedRowKeys.map((key) => String(key))
    if (projectIds.length === 0) {
      dispatch({ type: 'HIDE_BATCH_DELETE_MODAL' })
      return
    }
    try {
      dispatch({ type: 'SET_LOADING', payload: true })

      // 使用批量删除API
      const result = await projectService.batchDeleteProjects(projectIds)

      // 从状态中移除成功删除的项目
      const successfullyDeletedIds = projectIds.filter((id) => !result.failed_projects.includes(id))

      successfullyDeletedIds.forEach((id) => {
        removeProject(id)
      })

      // 清空选中状态
      dispatch({ type: 'CLEAR_SELECTION' })
      await fetchProjects()

      // 服务端返回的成功/失败计数必须如实反馈，全部失败时不能表现为静默成功
      const { success_count: successCount, failed_count: failedCount } = result
      const detail = describeBatchFailures(result.failures)
      if (failedCount === 0) {
        message.success(`成功删除 ${successCount} 个项目`)
      } else if (successCount === 0) {
        message.error(`删除失败：${failedCount} 个项目均未删除（${detail}）`)
      } else {
        message.warning(`部分成功：${successCount} 个已删除，${failedCount} 个失败（${detail}）`)
      }
    } catch (error) {
      Logger.error('批量删除项目失败:', error)
      message.error('批量删除项目失败')
    } finally {
      dispatch({ type: 'SET_LOADING', payload: false })
      dispatch({ type: 'HIDE_BATCH_DELETE_MODAL' })
    }
  }

  // 项目创建成功处理
  const handleCreateSuccess = (_project: unknown) => {
    dispatch({ type: 'TOGGLE_CREATE_DRAWER', payload: false })
    // 重新加载数据
    fetchProjects()
  }

  // 项目编辑成功处理
  const handleEditSuccess = () => {
    dispatch({ type: 'TOGGLE_EDIT_DRAWER', payload: false })
    dispatch({ type: 'SET_CURRENT_EDIT_PROJECT', payload: null })
    // 重新加载数据
    fetchProjects()
  }

  // 表格列配置
  const columns = useMemo<ColumnsType<Project>>(
    () => [
      {
        title: '项目名称',
        dataIndex: 'name',
        key: 'name',
        width: 180,
        minWidth: 120,
        ellipsis: { showTitle: false },
        fixed: 'left',
        render: (text: string, record: Project) => (
          <Tooltip title={text} placement="topLeft">
            <Button
              type="link"
              onClick={() => navigate(`/projects/${record.id}`)}
              style={{
                padding: 0,
                height: 'auto',
                textAlign: 'left',
                overflow: 'hidden',
                textOverflow: 'ellipsis',
                whiteSpace: 'nowrap',
                maxWidth: '100%',
                display: 'block',
                fontSize: 'inherit',
              }}
            >
              {text}
            </Button>
          </Tooltip>
        ),
      },
      {
        title: '类型',
        dataIndex: 'type',
        key: 'type',
        width: 90,
        render: (type: string) => (
          <Tag color={getProjectTypeColor(type as ProjectType)}>
            {getProjectTypeText(type as ProjectType)}
          </Tag>
        ),
      },
      {
        title: '状态',
        dataIndex: 'status',
        key: 'status',
        width: 90,
        render: (status: string) => (
          <Tag color={getProjectStatusColor(status)}>{getProjectStatusText(status)}</Tag>
        ),
      },
      {
        title: '任务数',
        dataIndex: 'task_count',
        key: 'task_count',
        width: 70,
        align: 'center',
        responsive: ['md'],
        render: (count: number) => <span>{count || 0}</span>,
      },
      {
        title: '创建者',
        dataIndex: 'created_by_username',
        key: 'created_by_username',
        width: 100,
        ellipsis: { showTitle: false },
        responsive: ['lg'],
        render: (username: string) => (
          <Tooltip title={username || '未知用户'} placement="topLeft">
            <span>{username || '未知'}</span>
          </Tooltip>
        ),
      },
      {
        title: '创建时间',
        dataIndex: 'created_at',
        key: 'created_at',
        width: 160,
        ellipsis: { showTitle: false },
        responsive: ['lg'],
        render: (date: string) => (
          <Tooltip title={formatDate(date)} placement="topLeft">
            <span>{formatDate(date)}</span>
          </Tooltip>
        ),
      },
      {
        title: '描述',
        dataIndex: 'description',
        key: 'description',
        ellipsis: { showTitle: false },
        width: 200,
        responsive: ['xl'],
        render: (text: string) => (
          <Tooltip title={text || '暂无描述'} placement="topLeft">
            <span
              style={{
                display: 'block',
                overflow: 'hidden',
                textOverflow: 'ellipsis',
                whiteSpace: 'nowrap',
              }}
            >
              {text || '-'}
            </span>
          </Tooltip>
        ),
      },
      {
        title: '操作',
        key: 'actions',
        width: 150,
        minWidth: 120,
        fixed: 'right',
        render: (_, record: Project) => (
          <div className="table-actions">
            <Space size="small" wrap>
              <Tooltip title="查看详情" placement="top">
                <Button
                  type="text"
                  icon={<EyeOutlined />}
                  size="small"
                  onClick={() => navigate(`/projects/${record.id}`)}
                  className="action-btn"
                />
              </Tooltip>
              <Tooltip title="创建任务" placement="top">
                <Button
                  type="text"
                  icon={<PlayCircleOutlined />}
                  size="small"
                  onClick={() => navigate(`/tasks/create?project_id=${record.id}`)}
                  className="action-btn hidden-sm"
                />
              </Tooltip>
              <Tooltip title="编辑" placement="top">
                <Button
                  type="text"
                  icon={<EditOutlined />}
                  size="small"
                  onClick={() => handleEdit(record)}
                  className="action-btn"
                />
              </Tooltip>
              <Tooltip title="删除" placement="top">
                <Button
                  type="text"
                  danger
                  icon={<DeleteOutlined />}
                  size="small"
                  onClick={() => handleDelete(record)}
                  className="action-btn"
                />
              </Tooltip>
            </Space>
          </div>
        ),
      },
    ],
    [handleDelete, handleEdit, navigate]
  )

  // 认证加载状态
  if (authLoading) {
    return (
      <div style={{ textAlign: 'center', padding: '50px' }}>
        <div style={{ fontSize: '16px', marginBottom: '16px' }}>正在验证登录状态...</div>
      </div>
    )
  }

  // 未认证状态
  if (!isAuthenticated) {
    return (
      <div style={{ textAlign: 'center', padding: '50px' }}>
        <h3>需要登录</h3>
        <Button type="primary" onClick={() => navigate('/login')}>
          去登录
        </Button>
      </div>
    )
  }

  return (
    <div style={{ padding: '24px' }}>
      {/* 页面标题 */}
      <div style={{ marginBottom: '24px' }}>
        <Space align="start">
          <h1
            style={{
              fontSize: '24px',
              fontWeight: 'bold',
              margin: 0,
              display: 'flex',
              alignItems: 'center',
              gap: '8px',
            }}
          >
            <FolderOutlined />
            项目管理
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
          {currentWorker ? `当前 Worker: ${currentWorker.name}` : '管理您的爬虫项目和代码'}
        </p>
      </div>

      {/* 工具栏 */}
      <Card style={{ marginBottom: 16 }}>
        <div
          style={{
            display: 'flex',
            justifyContent: 'space-between',
            alignItems: 'center',
            flexWrap: 'wrap',
            gap: '12px',
          }}
        >
          <Space wrap size="middle">
            <Button
              icon={<ReloadOutlined />}
              onClick={() => fetchProjects()}
              loading={uiState.loading}
            >
              刷新
            </Button>
            <Button
              type="primary"
              icon={<PlusOutlined />}
              onClick={() => dispatch({ type: 'TOGGLE_CREATE_DRAWER' })}
            >
              创建项目
            </Button>
            <Button
              danger
              icon={<DeleteOutlined />}
              onClick={handleBatchDelete}
              disabled={uiState.selectedRowKeys.length === 0}
            >
              批量删除{uiState.selectedRowKeys.length > 0 && ` (${uiState.selectedRowKeys.length})`}
            </Button>
          </Space>
          <Space wrap size="middle">
            <Select
              placeholder="类型"
              style={{ width: 100 }}
              allowClear
              value={typeFilter}
              onChange={handleTypeChange}
            >
              <Option value="file">文件</Option>
              <Option value="rule">规则</Option>
              <Option value="code">代码</Option>
            </Select>
            <Select
              placeholder="状态"
              style={{ width: 100 }}
              allowClear
              value={statusFilter}
              onChange={handleStatusChange}
            >
              <Option value="active">激活</Option>
              <Option value="inactive">未激活</Option>
              <Option value="archived">已归档</Option>
            </Select>
            {user?.is_admin && (
              <Select
                placeholder="创建者"
                style={{ width: 120 }}
                allowClear
                value={createdByFilter}
                onChange={handleCreatedByChange}
                loading={loadingUsers}
                showSearch
                optionFilterProp="label"
                options={userList.map((userItem) => ({
                  value: userItem.id,
                  label: userItem.username,
                }))}
                filterOption={(input, option) => {
                  const label = option?.label
                  if (typeof label !== 'string') return false
                  return label.toLowerCase().includes(input.toLowerCase())
                }}
              />
            )}
            <Search
              placeholder="搜索项目"
              style={{ width: 200 }}
              value={searchInputValue}
              onChange={handleSearchInput}
              onSearch={handleSearchChange}
              allowClear
            />
          </Space>
        </div>
      </Card>

      {/* 项目表格 */}
      <Card>
        <ResponsiveTable
          columns={columns}
          dataSource={projects}
          rowKey="id"
          loading={uiState.loading}
          minWidth={900}
          fixedActions={true}
          rowSelection={{
            preserveSelectedRowKeys: true,
            selectedRowKeys: uiState.selectedRowKeys,
            onChange: (newSelectedRowKeys: React.Key[], newSelectedRows: Project[]) => {
              const resolvedProjects = resolveSelectedProjects(newSelectedRowKeys, newSelectedRows)
              dispatch({
                type: 'SET_SELECTED_PROJECTS',
                payload: { keys: newSelectedRowKeys, projects: resolvedProjects },
              })
            },
            getCheckboxProps: (record: Project) => ({
              name: record.name,
            }),
          }}
          pagination={{
            current: currentPage,
            pageSize: pageSize,
            total: projectTotal,
            showSizeChanger: true,
            showQuickJumper: true,
            showTotal: (total, range) => `第 ${range[0]}-${range[1]} 条，共 ${total} 条记录`,
            pageSizeOptions: ['10', '20', '50', '100'],
            onChange: (page, size) => {
              handlePaginationChange(page, size || pageSize)
            },
            onShowSizeChange: (_, size) => {
              handlePaginationChange(1, size)
            },
          }}
          size="middle"
        />
      </Card>

      {/* 项目创建抽屉 */}
      <Suspense fallback={null}>
        <ProjectCreateDrawer
          open={uiState.createDrawerVisible}
          onClose={() => dispatch({ type: 'TOGGLE_CREATE_DRAWER', payload: false })}
          onSuccess={handleCreateSuccess}
        />
      </Suspense>

      {/* 项目编辑抽屉 */}
      <Suspense fallback={null}>
        <ProjectEditDrawer
          open={uiState.editDrawerVisible}
          onClose={() => {
            dispatch({ type: 'TOGGLE_EDIT_DRAWER', payload: false })
            dispatch({ type: 'SET_CURRENT_EDIT_PROJECT', payload: null })
          }}
          project={uiState.currentEditProject}
          onSuccess={handleEditSuccess}
        />
      </Suspense>

      {/* 单个删除确认Modal */}
      <Modal
        title="确认删除"
        open={uiState.deleteModalVisible}
        onOk={confirmDelete}
        onCancel={() => dispatch({ type: 'HIDE_DELETE_MODAL' })}
        okText="删除"
        cancelText="取消"
        okType="danger"
      >
        <ProjectDeleteWarning project={uiState.currentDeleteProject} />
      </Modal>

      {/* 批量删除确认Modal */}
      <Modal
        title="确认批量删除"
        open={uiState.batchDeleteModalVisible}
        onOk={confirmBatchDelete}
        onCancel={() => dispatch({ type: 'HIDE_BATCH_DELETE_MODAL' })}
        okText="删除"
        cancelText="取消"
        okType="danger"
        confirmLoading={uiState.loading}
      >
        <div>
          <p>确定要删除选中的 {uiState.selectedRowKeys.length} 个项目吗？此操作不可恢复。</p>
          <div style={{ marginTop: 8 }}>
            <strong>将要删除的项目：</strong>
            <ul style={{ marginTop: 4, marginBottom: 0 }}>
              {uiState.selectedProjects.slice(0, 5).map((project) => (
                <li key={project.id}>{project.name}</li>
              ))}
              {uiState.selectedRowKeys.length > 5 && (
                <li>... 还有 {uiState.selectedRowKeys.length - 5} 个项目</li>
              )}
            </ul>
          </div>
        </div>
      </Modal>
    </div>
  )
}

export default ProjectList
