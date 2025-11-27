import React, { useEffect, useReducer, useRef, useState, useCallback, Suspense, lazy } from 'react'
import {
  Button,
  Space,
  Tag,
  Modal,
  Input,
  Select,
  Card,
  Tooltip
} from 'antd'
import showNotification from '@/utils/notification'
import ResponsiveTable from '@/components/common/ResponsiveTable'
import {
  PlusOutlined,
  EditOutlined,
  DeleteOutlined,
  PlayCircleOutlined,
  EyeOutlined,
  SearchOutlined
} from '@ant-design/icons'
import { useNavigate } from 'react-router-dom'
import { useProjects } from '@/stores/projectStore'
import { projectService } from '@/services/projects'
import { formatDate } from '@/utils/helpers'
import {
  getProjectTypeText,
  getProjectStatusText,
  getProjectTypeColor,
  getProjectStatusColor
} from '@/utils/projectUtils'
import useAuth from '@/hooks/useAuth'
import { userService, type SimpleUser } from '@/services/users'
import type { Project, ProjectListParams } from '@/types'
import type { ColumnsType } from 'antd/es/table'

const { Search } = Input
const { Option } = Select

const ProjectCreateDrawer = lazy(() => import('@/components/projects/ProjectCreateDrawer'))
const ProjectEditDrawer = lazy(() => import('@/components/projects/ProjectEditDrawer'))

// UI状态类型定义
interface UIState {
  loading: boolean
  createDrawerVisible: boolean
  editDrawerVisible: boolean
  selectedRowKeys: React.Key[]
  selectedProjects: Project[]
  deleteModalVisible: boolean
  batchDeleteModalVisible: boolean
  currentDeleteProject: Project | null
  currentEditProject: Project | null
}

// Action类型定义
type UIAction =
  | { type: 'SET_LOADING'; payload: boolean }
  | { type: 'TOGGLE_CREATE_DRAWER'; payload?: boolean }
  | { type: 'TOGGLE_EDIT_DRAWER'; payload?: boolean }
  | { type: 'SET_CURRENT_EDIT_PROJECT'; payload: Project | null }
  | { type: 'SET_SELECTED_PROJECTS'; payload: { keys: React.Key[]; projects: Project[] } }
  | { type: 'SHOW_DELETE_MODAL'; payload: Project }
  | { type: 'HIDE_DELETE_MODAL' }
  | { type: 'SHOW_BATCH_DELETE_MODAL' }
  | { type: 'HIDE_BATCH_DELETE_MODAL' }
  | { type: 'CLEAR_SELECTION' }
  | { type: 'REMOVE_SELECTED_PROJECT'; payload: number }

// 初始状态
const initialUIState: UIState = {
  loading: false,
  createDrawerVisible: false,
  editDrawerVisible: false,
  selectedRowKeys: [],
  selectedProjects: [],
  deleteModalVisible: false,
  batchDeleteModalVisible: false,
  currentDeleteProject: null,
  currentEditProject: null
}

// Reducer函数
function uiReducer(state: UIState, action: UIAction): UIState {
  switch (action.type) {
    case 'SET_LOADING':
      return { ...state, loading: action.payload }
    case 'TOGGLE_CREATE_DRAWER':
      return { 
        ...state, 
        createDrawerVisible: action.payload ?? !state.createDrawerVisible 
      }
    case 'TOGGLE_EDIT_DRAWER':
      return { 
        ...state, 
        editDrawerVisible: action.payload ?? !state.editDrawerVisible 
      }
    case 'SET_CURRENT_EDIT_PROJECT':
      return {
        ...state,
        currentEditProject: action.payload
      }
    case 'SET_SELECTED_PROJECTS':
      return {
        ...state,
        selectedRowKeys: action.payload.keys,
        selectedProjects: action.payload.projects
      }
    case 'SHOW_DELETE_MODAL':
      return {
        ...state,
        deleteModalVisible: true,
        currentDeleteProject: action.payload
      }
    case 'HIDE_DELETE_MODAL':
      return {
        ...state,
        deleteModalVisible: false,
        currentDeleteProject: null
      }
    case 'SHOW_BATCH_DELETE_MODAL':
      return {
        ...state,
        batchDeleteModalVisible: true
      }
    case 'HIDE_BATCH_DELETE_MODAL':
      return {
        ...state,
        batchDeleteModalVisible: false
      }
    case 'CLEAR_SELECTION':
      return {
        ...state,
        selectedRowKeys: [],
        selectedProjects: []
      }
    case 'REMOVE_SELECTED_PROJECT':
      return {
        ...state,
        selectedRowKeys: state.selectedRowKeys.filter(key => key !== action.payload),
        selectedProjects: state.selectedProjects.filter(p => p.id !== action.payload)
      }
    default:
      return state
  }
}

const ProjectList: React.FC = () => {
  const navigate = useNavigate()
  const { isAuthenticated, loading: authLoading, user } = useAuth()
  const [uiState, dispatch] = useReducer(uiReducer, initialUIState)
  const currentUserIdRef = useRef<number | undefined>(user?.id)
  const [userList, setUserList] = useState<SimpleUser[]>([])
  const [loadingUsers, setLoadingUsers] = useState(false)
  const [projects, setProjectList] = useState<Project[]>([])
  const [searchInput, setSearchInput] = useState('')
  const [queryParams, setQueryParams] = useState<ProjectListParams>({
    page: 1,
    size: 10
  })
  const [tablePagination, setTablePagination] = useState({
    current: 1,
    pageSize: 10,
    total: 0
  })

  const {
    setProjects,
    setPagination,
    removeProject
  } = useProjects()

  // 获取用户列表（仅管理员需要）
  const fetchUserList = async () => {
    if (!user?.is_admin) return
    
    setLoadingUsers(true)
    try {
      const users = await userService.getSimpleUserList()
      setUserList(users)
    } catch (error) {
      console.error('获取用户列表失败:', error)
    } finally {
      setLoadingUsers(false)
    }
  }

  const fetchProjects = useCallback(async (params: ProjectListParams) => {
    dispatch({ type: 'SET_LOADING', payload: true })
    try {
      const response = await projectService.getProjects(params)
      const items = response.items || []

      setProjectList(items)
      setProjects(items)
      setTablePagination({
        current: response.page || params.page || 1,
        pageSize: response.size || params.size || 10,
        total: response.total || 0
      })
      setPagination({
        current: response.page || params.page || 1,
        pageSize: response.size || params.size || 10,
        total: response.total || 0
      })
    } catch (error) {
      console.error('[Error] Error fetching projects:', error)
      setProjectList([])
      setProjects([])
      setTablePagination(prev => ({
        ...prev,
        total: 0
      }))
      setPagination({
        current: 1,
        pageSize: params.size || tablePagination.pageSize,
        total: 0
      })
    } finally {
      dispatch({ type: 'SET_LOADING', payload: false })
    }
  }, [setProjects, setPagination, tablePagination.pageSize])

  // 初始加载
  useEffect(() => {
    if (!isAuthenticated && !authLoading) {
      setProjectList([])
      setProjects([])
      setTablePagination(prev => ({
        ...prev,
        current: 1,
        total: 0
      }))
      setPagination({
        current: 1,
        pageSize: queryParams.size || 10,
        total: 0
      })
    }
  }, [isAuthenticated, authLoading, queryParams.size, setPagination, setProjects])

  useEffect(() => {
    if (!user) {
      currentUserIdRef.current = undefined
      return
    }

    if (user.id !== currentUserIdRef.current) {
      currentUserIdRef.current = user.id
      const defaultSize = queryParams.size || 10
      setSearchInput('')
      setProjectList([])
      setProjects([])
      setTablePagination({
        current: 1,
        pageSize: defaultSize,
        total: 0
      })
      setPagination({
        current: 1,
        pageSize: defaultSize,
        total: 0
      })
      setQueryParams({
        page: 1,
        size: defaultSize,
        type: undefined,
        status: undefined,
        tag: undefined,
        search: undefined,
        created_by: user.is_admin ? undefined : user.id
      })
      return
    }

    if (!user.is_admin && queryParams.created_by !== user.id) {
      setQueryParams(prev => ({
        ...prev,
        page: 1,
        created_by: user.id
      }))
    }
  }, [user?.id, user?.is_admin, queryParams.size, queryParams.created_by, setPagination, setProjects])

  useEffect(() => {
    if (!isAuthenticated || authLoading || !user) {
      return
    }

    const effectiveSize = queryParams.size && queryParams.size > 0 ? queryParams.size : 10
    const params: ProjectListParams = {
      ...queryParams,
      page: queryParams.page || 1,
      size: effectiveSize,
      created_by: user.is_admin ? queryParams.created_by : user.id
    }

    fetchProjects(params)
  }, [queryParams, user?.id, user?.is_admin, isAuthenticated, authLoading, fetchProjects])

  // 获取用户列表（管理员专用）
  useEffect(() => {
    if (user?.is_admin && isAuthenticated && !authLoading) {
      fetchUserList()
    }
  }, [user?.is_admin, isAuthenticated, authLoading])

  // 删除单个项目
  const handleDelete = (project: Project) => {
    dispatch({ type: 'SHOW_DELETE_MODAL', payload: project })
  }

  // 编辑项目
  const handleEdit = async (project: Project) => {
    try {
      dispatch({ type: 'SET_LOADING', payload: true })
      // 获取完整的项目详情
      const fullProject = await projectService.getProject(project.id)
      dispatch({ type: 'SET_CURRENT_EDIT_PROJECT', payload: fullProject })
      dispatch({ type: 'TOGGLE_EDIT_DRAWER', payload: true })
    } catch (error) {
      // 错误提示由拦截器统一处理
    } finally {
      dispatch({ type: 'SET_LOADING', payload: false })
    }
  }

  // 确认删除单个项目
  const confirmDelete = async () => {
    if (!uiState.currentDeleteProject) return

    try {
      await projectService.deleteProject(uiState.currentDeleteProject.id)
      removeProject(uiState.currentDeleteProject.id)

      const remainingItems = projects.filter(p => p.id !== uiState.currentDeleteProject.id)
      setProjectList(remainingItems)

      // 如果删除的项目在选中列表中，需要更新选中状态
      if (uiState.selectedRowKeys.includes(uiState.currentDeleteProject.id)) {
        dispatch({ type: 'REMOVE_SELECTED_PROJECT', payload: uiState.currentDeleteProject.id })
      }

      if (remainingItems.length === 0) {
        setQueryParams(prev => ({
          ...prev,
          page: Math.max(1, (prev.page || tablePagination.current || 1) - 1)
        }))
      } else {
        setQueryParams(prev => ({ ...prev }))
      }
    } catch (error) {
      // 错误提示由拦截器统一处理
    } finally {
      dispatch({ type: 'HIDE_DELETE_MODAL' })
    }
  }

  // 批量删除项目
  const handleBatchDelete = () => {
    if (uiState.selectedProjects.length === 0) {
      return
    }

    dispatch({ type: 'SHOW_BATCH_DELETE_MODAL' })
  }

  // 确认批量删除
  const confirmBatchDelete = async () => {
    try {
      dispatch({ type: 'SET_LOADING', payload: true })

      // 使用批量删除API
      const projectIds = uiState.selectedProjects.map(project => project.id)
      const result = await projectService.batchDeleteProjects(projectIds)

      // 从状态中移除成功删除的项目
      const successfullyDeletedIds = projectIds.filter(id =>
        !result.failed_projects.includes(id)
      )

      successfullyDeletedIds.forEach(id => {
        removeProject(id)
      })

      const remainingAfterDelete = projects.filter(p => !successfullyDeletedIds.includes(p.id))
      setProjectList(remainingAfterDelete)

      // 清空选中状态
      dispatch({ type: 'CLEAR_SELECTION' })

      if (remainingAfterDelete.length === 0) {
        setQueryParams(prev => ({
          ...prev,
          page: Math.max(1, (prev.page || tablePagination.current || 1) - 1)
        }))
      } else {
        setQueryParams(prev => ({ ...prev }))
      }
    } catch (error) {
      // 错误提示由拦截器统一处理
    } finally {
      dispatch({ type: 'SET_LOADING', payload: false })
      dispatch({ type: 'HIDE_BATCH_DELETE_MODAL' })
    }
  }

  // 项目创建成功处理
  const handleCreateSuccess = (project: unknown) => {
    dispatch({ type: 'TOGGLE_CREATE_DRAWER', payload: false })
    setQueryParams(prev => ({ ...prev }))
  }

  // 项目编辑成功处理
  const handleEditSuccess = () => {
    dispatch({ type: 'TOGGLE_EDIT_DRAWER', payload: false })
    dispatch({ type: 'SET_CURRENT_EDIT_PROJECT', payload: null })
    setQueryParams(prev => ({ ...prev }))
  }

  const handleSearch = (value: string) => {
    const trimmed = value.trim()
    setSearchInput(value)
    setQueryParams(prev => ({
      ...prev,
      page: 1,
      search: trimmed ? trimmed : undefined
    }))
  }

  const handleTypeChange = (value: string | undefined) => {
    setQueryParams(prev => ({
      ...prev,
      page: 1,
      type: value as ProjectListParams['type'] | undefined
    }))
  }

  const handleStatusChange = (value: string | undefined) => {
    setQueryParams(prev => ({
      ...prev,
      page: 1,
      status: value as ProjectListParams['status'] | undefined
    }))
  }

  const handleCreatedByChange = (value: number | undefined) => {
    setQueryParams(prev => ({
      ...prev,
      page: 1,
      created_by: value
    }))
  }

  const handlePaginationChange = (page: number, size: number) => {
    setQueryParams(prev => ({
      ...prev,
      page,
      size
    }))
  }



  // 表格列配置
  const columns: ColumnsType<Project> = [
    {
      title: '项目名称',
      dataIndex: 'name',
      key: 'name',
      width: 200,
      minWidth: 120, // 最小宽度
      ellipsis: true,
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
              fontSize: 'inherit'
            }}
          >
            {text}
          </Button>
        </Tooltip>
      )
    },
    {
      title: '类型',
      dataIndex: 'type',
      key: 'type',
      width: 100,
      responsive: ['md'], // 中等屏幕及以上显示
      render: (type: string) => (
        <Tag color={getProjectTypeColor(type as any)}>
          {getProjectTypeText(type as any)}
        </Tag>
      )
    },
    {
      title: '状态',
      dataIndex: 'status',
      key: 'status',
      width: 100,
      ellipsis: true,
      render: (status: string) => (
        <Tooltip title={getProjectStatusText(status)} placement="top">
          <Tag color={getProjectStatusColor(status)}>
            {getProjectStatusText(status)}
          </Tag>
        </Tooltip>
      )
    },
    {
      title: '描述',
      dataIndex: 'description',
      key: 'description',
      ellipsis: true,
      width: 250,
      responsive: ['lg'], // 大屏幕及以上显示
      render: (text: string) => (
        <Tooltip title={text || '暂无描述'} placement="topLeft">
          <span style={{
            display: 'block',
            overflow: 'hidden',
            textOverflow: 'ellipsis',
            whiteSpace: 'nowrap'
          }}>
            {text || '-'}
          </span>
        </Tooltip>
      )
    },
    {
      title: '创建者',
      dataIndex: 'created_by_username',
      key: 'created_by_username',
      width: 120,
      ellipsis: true,
      responsive: ['lg'], // 大屏幕及以上显示
      render: (username: string, record: Project) => (
        <Tooltip title={`创建者: ${username || '未知用户'}`} placement="top">
          <span style={{
            display: 'block',
            overflow: 'hidden',
            textOverflow: 'ellipsis',
            whiteSpace: 'nowrap'
          }}>
            {username || '未知用户'}
          </span>
        </Tooltip>
      )
    },
    {
      title: '任务数',
      dataIndex: 'task_count',
      key: 'task_count',
      width: 80,
      ellipsis: true,
      responsive: ['lg'], // 大屏幕及以上显示
      render: (count: number) => (
        <Tooltip title={`任务数量: ${count || 0}`} placement="top">
          <span>{count || 0}</span>
        </Tooltip>
      )
    },
    {
      title: '创建时间',
      dataIndex: 'created_at',
      key: 'created_at',
      width: 180,
      ellipsis: true,
      responsive: ['xl'], // 超大屏幕及以上显示
      render: (date: string) => (
        <Tooltip title={`创建时间: ${formatDate(date)}`} placement="topLeft">
          <span style={{
            display: 'block',
            overflow: 'hidden',
            textOverflow: 'ellipsis',
            whiteSpace: 'nowrap'
          }}>
            {formatDate(date)}
          </span>
        </Tooltip>
      )
    },
    {
      title: '操作',
      key: 'actions',
      width: 160,
      minWidth: 120,
      fixed: 'right', // 固定在右侧
      render: (_, record: Project) => (
        <div className="table-actions">
          <Space size="small" wrap>
            <Tooltip title="查看详情">
              <Button
                type="text"
                icon={<EyeOutlined />}
                size="small"
                onClick={() => navigate(`/projects/${record.id}`)}
                className="action-btn"
              />
            </Tooltip>
            <Tooltip title="创建任务">
              <Button
                type="text"
                icon={<PlayCircleOutlined />}
                size="small"
                onClick={() => navigate(`/tasks/create?project_id=${record.id}`)}
                className="action-btn hidden-sm" // 小屏幕隐藏
              />
            </Tooltip>
            <Tooltip title="编辑">
              <Button
                type="text"
                icon={<EditOutlined />}
                size="small"
                onClick={() => handleEdit(record)}
                className="action-btn"
              />
            </Tooltip>
            <Tooltip title="删除">
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
      )
    }
  ]

  // 认证加载状态
  if (authLoading) {
    return (
      <div style={{ textAlign: 'center', padding: '50px' }}>
        <div style={{ fontSize: '16px', marginBottom: '16px' }}>
          正在验证登录状态...
        </div>
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
    <Card>
      {/* 工具栏 */}
      <div style={{ marginBottom: 16 }}>
        <div className="toolbar-container">
          {/* 主要操作按钮 */}
          <div className="toolbar-actions">
            <Space wrap>
              <Button
                type="primary"
                icon={<PlusOutlined />}
                onClick={() => dispatch({ type: 'TOGGLE_CREATE_DRAWER' })}
                size="middle"
              >
                <span className="hidden-xs">创建项目</span>
              </Button>

              <Button
                danger
                icon={<DeleteOutlined />}
                onClick={handleBatchDelete}
                disabled={uiState.selectedRowKeys.length === 0}
                size="middle"
              >
                <span className="hidden-xs">批量删除</span>
                {uiState.selectedRowKeys.length > 0 && ` (${uiState.selectedRowKeys.length})`}
              </Button>
            </Space>
          </div>

          {/* 筛选和搜索 */}
          <div className="toolbar-filters">
            <Space wrap>
              <Search
                placeholder="搜索项目"
                style={{ width: 200, minWidth: 150 }}
                onSearch={handleSearch}
                value={searchInput}
                onChange={(e) => {
                  const value = e.target.value
                  setSearchInput(value)
                  if (!value.trim()) {
                    setQueryParams(prev => ({
                      ...prev,
                      page: 1,
                      search: undefined
                    }))
                  }
                }}
                allowClear
                size="middle"
              />

              <Select
                placeholder="类型"
                style={{ width: 100, minWidth: 80 }}
                allowClear
                value={queryParams.type}
                onChange={handleTypeChange}
                size="middle"
              >
                <Option value="file">文件</Option>
                <Option value="rule">规则</Option>
                <Option value="code">代码</Option>
              </Select>

              <Select
                placeholder="状态"
                style={{ width: 100, minWidth: 80 }}
                allowClear
                value={queryParams.status}
                onChange={handleStatusChange}
                size="middle"
                className="hidden-xs"
              >
                <Option value="active">活跃</Option>
                <Option value="inactive">非活跃</Option>
                <Option value="error">错误</Option>
              </Select>

              {/* 用户筛选器（仅管理员可见） */}
              {user?.is_admin && (
                <Select
                  placeholder="创建者"
                  style={{ width: 120, minWidth: 100 }}
                  allowClear
                  value={queryParams.created_by}
                  onChange={handleCreatedByChange}
                  size="middle"
                  loading={loadingUsers}
                  showSearch
                  filterOption={(input, option) =>
                    (option?.children as string)?.toLowerCase().includes(input.toLowerCase())
                  }
                >
                  {userList.map((user) => (
                    <Option key={user.id} value={user.id}>
                      {user.username}
                    </Option>
                  ))}
                </Select>
              )}
            </Space>
          </div>
        </div>
      </div>

      {/* 项目表格 */}
      <ResponsiveTable
        columns={columns}
        dataSource={projects}
        rowKey="id"
        loading={uiState.loading}
        minWidth={1000}
        fixedActions={true}
        rowSelection={{
          selectedRowKeys: uiState.selectedRowKeys,
          onChange: (newSelectedRowKeys: React.Key[], newSelectedRows: Project[]) => {
            dispatch({ 
              type: 'SET_SELECTED_PROJECTS', 
              payload: { keys: newSelectedRowKeys, projects: newSelectedRows } 
            })
          },
          getCheckboxProps: (record: Project) => ({
            name: record.name,
          }),
        }}
        pagination={{
          current: tablePagination.current,
          pageSize: tablePagination.pageSize,
          total: tablePagination.total,
          showSizeChanger: true,
          showQuickJumper: true,
          showTotal: (total, range) =>
            `第 ${range[0]}-${range[1]} 条，共 ${total} 条记录`,
          pageSizeOptions: ['10', '20', '50', '100'],
          onChange: (page, size) => {
            handlePaginationChange(page, size || tablePagination.pageSize)
          },
          onShowSizeChange: (current, size) => {
            handlePaginationChange(1, size)
          }
        }}
        size="middle"
      />

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
        <p>确定要删除项目 "{uiState.currentDeleteProject?.name}" 吗？此操作不可恢复。</p>
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
          <p>确定要删除选中的 {uiState.selectedProjects.length} 个项目吗？此操作不可恢复。</p>
          <div style={{ marginTop: 8 }}>
            <strong>将要删除的项目：</strong>
            <ul style={{ marginTop: 4, marginBottom: 0 }}>
              {uiState.selectedProjects.slice(0, 5).map(project => (
                <li key={project.id}>{project.name}</li>
              ))}
              {uiState.selectedProjects.length > 5 && (
                <li>... 还有 {uiState.selectedProjects.length - 5} 个项目</li>
              )}
            </ul>
          </div>
        </div>
      </Modal>
    </Card>
  )
}

export default ProjectList
