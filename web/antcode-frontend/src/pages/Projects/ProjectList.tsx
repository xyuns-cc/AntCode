import type React from 'react'
import { useEffect, useReducer, useRef, useState, useCallback, Suspense, lazy, useMemo } from 'react'
import {
  Button,
  Space,
  Tag,
  Modal,
  Input,
  Select,
  Card,
  Tooltip,
  Upload,
  Checkbox,
  Form,
  Typography
} from 'antd'
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
  UploadOutlined
} from '@ant-design/icons'
import { useNavigate } from 'react-router-dom'
import { useProjects } from '@/stores/projectStore'
import { useWorkerStore } from '@/stores/workerStore'
import { projectService } from '@/services/projects'
import { formatDate } from '@/utils/format'
import {
  getProjectTypeText,
  getProjectStatusText,
  getProjectTypeColor,
  getProjectStatusColor
} from '@/utils/projectUtils'
import EnvSelector from '@/components/runtimes/EnvSelector'
import type { EnvironmentConfig } from '@/components/runtimes/EnvSelector'
import showNotification from '@/utils/notification'
import useAuth from '@/hooks/useAuth'
import { userService, type SimpleUser } from '@/services/users'
import { isAbortError } from '@/utils/helpers'
import type { Project, ProjectListParams, ProjectType } from '@/types'
import type { UploadFile } from 'antd'
import type { ColumnsType } from 'antd/es/table'

const { Search } = Input
const { Option } = Select
const { Text } = Typography

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
  | { type: 'REMOVE_SELECTED_PROJECT'; payload: string }

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
  const { currentWorker, workers, refreshWorkers } = useWorkerStore()
  const [uiState, dispatch] = useReducer(uiReducer, initialUIState)
  const currentUserIdRef = useRef<string | undefined>(user?.id)
  const mountedRef = useRef(true)
  const controllersRef = useRef<Record<string, AbortController | null>>({})
  const [userList, setUserList] = useState<SimpleUser[]>([])
  const [loadingUsers, setLoadingUsers] = useState(false)
  
  // 所有项目数据（一次性加载）
  const [allProjects, setAllProjects] = useState<Project[]>([])
  
  // 前端筛选条件
  const [searchQuery, setSearchQuery] = useState('')
  const [typeFilter, setTypeFilter] = useState<ProjectListParams['type'] | undefined>(undefined)
  const [statusFilter, setStatusFilter] = useState<ProjectListParams['status'] | undefined>(undefined)
  const [createdByFilter, setCreatedByFilter] = useState<string | undefined>(undefined)
  
  // 前端分页
  const [currentPage, setCurrentPage] = useState(1)
  const [pageSize, setPageSize] = useState(10)
  const [importModalVisible, setImportModalVisible] = useState(false)
  const [importSubmitting, setImportSubmitting] = useState(false)
  const [importProjectFiles, setImportProjectFiles] = useState<UploadFile[]>([])
  const [importProjectName, setImportProjectName] = useState('')
  const [importEntryPoint, setImportEntryPoint] = useState('')
  const [importOverwriteExisting, setImportOverwriteExisting] = useState(false)
  const [importEnvConfig, setImportEnvConfig] = useState<EnvironmentConfig | null>(null)

  const {
    setProjects,
    setPagination,
    removeProject
  } = useProjects()

  const resolveSelectedProjects = useCallback((
    selectedKeys: React.Key[],
    currentRows: Project[]
  ) => {
    if (selectedKeys.length === 0) {
      return []
    }
    // 保持跨分页勾选时的完整选中列表
    const selectedKeySet = new Set(selectedKeys.map(key => String(key)))
    let resolvedProjects = allProjects.filter(project => selectedKeySet.has(project.id))
    if (resolvedProjects.length !== selectedKeySet.size) {
      const knownIds = new Set(resolvedProjects.map(project => project.id))
      const mergedProjects = [...resolvedProjects]
      currentRows.forEach(project => {
        if (selectedKeySet.has(project.id) && !knownIds.has(project.id)) {
          mergedProjects.push(project)
          knownIds.add(project.id)
        }
      })
      resolvedProjects = mergedProjects
    }
    return resolvedProjects
  }, [allProjects])

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
      // 错误由拦截器处理
    } finally {
      if (mountedRef.current && isLatestController('users', controller)) {
        setLoadingUsers(false)
      }
    }
  }, [user?.is_admin, createController, isLatestController])

  // 智能加载所有项目数据
  const fetchAllProjects = useCallback(async () => {
    if (!isAuthenticated || !user) return

    const controller = createController('projects')
    const requestConfig = { signal: controller.signal }

    dispatch({ type: 'SET_LOADING', payload: true })
    try {
      // 构建基础参数（非管理员只能看自己的项目）
      const baseParams: ProjectListParams = {
        page: 1,
        size: 100,
        created_by: user.is_admin ? undefined : user.id,
        worker_id: currentWorker?.id  // 按 Worker 筛选
      }

      // 先获取第一页，查看总数
      const firstPageResponse = await projectService.getProjects(baseParams, requestConfig)
      const totalCount = firstPageResponse.total
      const allItems = [...(firstPageResponse.items || [])]

      // 如果总数大于100，分批加载（最多加载前10页，即1000条数据）
      if (totalCount > 100) {
        const totalPages = Math.ceil(totalCount / 100)
        const pagesToLoad = Math.min(totalPages, 10)

        const promises = []
        for (let page = 2; page <= pagesToLoad; page++) {
          promises.push(projectService.getProjects({ ...baseParams, page }, requestConfig))
        }

        const results = await Promise.all(promises)
        results.forEach(response => {
          allItems.push(...(response.items || []))
        })
      }

      if (!mountedRef.current || !isLatestController('projects', controller)) return
      setAllProjects(allItems)
      setProjects(allItems)
    } catch (error) {
      if (isAbortError(error)) return
      // 错误由拦截器处理
      if (!mountedRef.current || !isLatestController('projects', controller)) return
      setAllProjects([])
      setProjects([])
    } finally {
      if (mountedRef.current && isLatestController('projects', controller)) {
        dispatch({ type: 'SET_LOADING', payload: false })
      }
    }
  }, [isAuthenticated, user, setProjects, currentWorker?.id, createController, isLatestController])

  // 前端筛选和分页逻辑
  const filteredAndPaginatedProjects = useMemo(() => {
    let filtered = [...allProjects]

    // 应用类型筛选
    if (typeFilter) {
      filtered = filtered.filter(project => project.type === typeFilter)
    }

    // 应用状态筛选
    if (statusFilter) {
      filtered = filtered.filter(project => project.status === statusFilter)
    }

    // 应用创建者筛选（仅管理员可用）
    if (createdByFilter !== undefined && user?.is_admin) {
      filtered = filtered.filter(project => project.created_by === createdByFilter)
    }

    // 应用搜索（项目名称、描述）
    if (searchQuery) {
      const lowerQuery = searchQuery.toLowerCase().trim()
      filtered = filtered.filter(project => 
        project.name?.toLowerCase().includes(lowerQuery) ||
        project.description?.toLowerCase().includes(lowerQuery)
      )
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
  }, [allProjects, typeFilter, statusFilter, createdByFilter, searchQuery, currentPage, pageSize, user?.is_admin])

  // 同步分页信息到 store（使用 useEffect 避免在渲染期间更新状态）
  useEffect(() => {
    setPagination({
      current: currentPage,
      pageSize: pageSize,
      total: filteredAndPaginatedProjects.total
    })
  }, [currentPage, pageSize, filteredAndPaginatedProjects.total, setPagination])

  // 初始加载
  useEffect(() => {
    if (!isAuthenticated && !authLoading) {
      setAllProjects([])
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
      setSearchQuery('')
      setTypeFilter(undefined)
      setStatusFilter(undefined)
      setCreatedByFilter(undefined)
      setCurrentPage(1)
      fetchAllProjects()
    }
  }, [user, fetchAllProjects])

  useEffect(() => {
    if (!isAuthenticated || authLoading || !user) {
      return
    }

    // 首次加载或 Worker 切换时重新加载
    fetchAllProjects()
  }, [isAuthenticated, authLoading, user, fetchAllProjects])

  // 获取用户列表（管理员专用）
  useEffect(() => {
    if (user?.is_admin && isAuthenticated && !authLoading) {
      fetchUserList()
    }
  }, [user?.is_admin, isAuthenticated, authLoading, fetchUserList])

  // 处理筛选变化时重置到第一页
  const handleSearchChange = (value: string) => {
    setSearchQuery(value)
    setCurrentPage(1)
  }

  const handleSearchInput = (e: React.ChangeEvent<HTMLInputElement>) => {
    const value = e.target.value
    setSearchQuery(value)
    if (!value.trim()) {
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

  const resetImportState = useCallback(() => {
    setImportProjectFiles([])
    setImportProjectName('')
    setImportEntryPoint('')
    setImportOverwriteExisting(false)
    setImportEnvConfig(null)
  }, [])

  const isCompressedFile = useCallback((filename?: string) => {
    const name = (filename || '').toLowerCase()
    return name.endsWith('.zip') || name.endsWith('.tar.gz') || name.endsWith('.tar')
  }, [])

  const openImportModal = useCallback(() => {
    refreshWorkers().catch(() => undefined)
    setImportEnvConfig({
      location: 'worker',
      workerId: currentWorker?.id,
      scope: 'private',
      useExisting: false,
      existingEnvName: undefined,
      pythonVersion: '3.11',
      envName: undefined,
      envDescription: undefined,
    })
    setImportModalVisible(true)
  }, [currentWorker?.id, refreshWorkers])

  const handleImportSubmit = useCallback(async () => {
    const projectFile = importProjectFiles[0]?.originFileObj as File | undefined
    if (!projectFile) {
      showNotification('error', '请选择项目文件或压缩包')
      return
    }
    const name = importProjectName.trim()
    const entryPoint = importEntryPoint.trim()

    if (isCompressedFile(projectFile.name) && !entryPoint) {
      showNotification('error', '压缩包必须填写入口文件')
      return
    }

    if (!importEnvConfig) {
      showNotification('error', '请配置运行环境')
      return
    }

    if (!importEnvConfig.workerId) {
      showNotification('error', '请选择 Worker')
      return
    }

    if (importEnvConfig.useExisting) {
      if (!importEnvConfig.existingEnvName) {
        showNotification('error', '请选择要使用的环境')
        return
      }
    } else if (!importEnvConfig.pythonVersion) {
      showNotification('error', '创建新环境时必须指定 Python 版本')
      return
    }

    try {
      setImportSubmitting(true)
      await projectService.importProject({
        file: projectFile,
        name: name || undefined,
        entry_point: entryPoint || undefined,
        runtime_scope: importEnvConfig.scope,
        worker_id: importEnvConfig.workerId,
        use_existing_env: importEnvConfig.useExisting,
        existing_env_name: importEnvConfig.existingEnvName,
        python_version: importEnvConfig.pythonVersion,
        env_name: importEnvConfig.envName,
        env_description: importEnvConfig.envDescription,
        overwrite_existing: importOverwriteExisting && Boolean(name),
      })
      showNotification('success', '导入成功')
      setImportModalVisible(false)
      resetImportState()
      fetchAllProjects()
    } catch {
      // 错误提示由拦截器统一处理
    } finally {
      setImportSubmitting(false)
    }
  }, [
    fetchAllProjects,
    importEnvConfig,
    importEntryPoint,
    importOverwriteExisting,
    importProjectFiles,
    importProjectName,
    isCompressedFile,
    resetImportState,
  ])

  // 删除单个项目
  const handleDelete = useCallback((project: Project) => {
    dispatch({ type: 'SHOW_DELETE_MODAL', payload: project })
  }, [dispatch])

  // 编辑项目
  const handleEdit = useCallback(async (project: Project) => {
    try {
      dispatch({ type: 'SET_LOADING', payload: true })
      // 获取完整的项目详情
      const fullProject = await projectService.getProject(project.id)
      dispatch({ type: 'SET_CURRENT_EDIT_PROJECT', payload: fullProject })
      dispatch({ type: 'TOGGLE_EDIT_DRAWER', payload: true })
    } catch {
      // 错误提示由拦截器统一处理
    } finally {
      dispatch({ type: 'SET_LOADING', payload: false })
    }
  }, [dispatch])

  // 确认删除单个项目
  const confirmDelete = async () => {
    if (!uiState.currentDeleteProject) return

    try {
      await projectService.deleteProject(uiState.currentDeleteProject.id)
      removeProject(uiState.currentDeleteProject.id)

      // 直接从本地状态移除
      setAllProjects(prev => prev.filter(p => p.id !== uiState.currentDeleteProject!.id))

      // 如果删除的项目在选中列表中，需要更新选中状态
      if (uiState.selectedRowKeys.includes(uiState.currentDeleteProject.id)) {
        dispatch({ type: 'REMOVE_SELECTED_PROJECT', payload: uiState.currentDeleteProject.id })
      }
    } catch {
      // 错误提示由拦截器统一处理
    } finally {
      dispatch({ type: 'HIDE_DELETE_MODAL' })
    }
  }

  // 批量删除项目
  const handleBatchDelete = () => {
    if (uiState.selectedRowKeys.length === 0) {
      return
    }

    dispatch({ type: 'SHOW_BATCH_DELETE_MODAL' })
  }

  // 确认批量删除
  const confirmBatchDelete = async () => {
    const projectIds = uiState.selectedRowKeys.map(key => String(key))
    if (projectIds.length === 0) {
      dispatch({ type: 'HIDE_BATCH_DELETE_MODAL' })
      return
    }
    try {
      dispatch({ type: 'SET_LOADING', payload: true })

      // 使用批量删除API
      const result = await projectService.batchDeleteProjects(projectIds)

      // 从状态中移除成功删除的项目
      const successfullyDeletedIds = projectIds.filter(id =>
        !result.failed_projects.includes(id)
      )

      successfullyDeletedIds.forEach(id => {
        removeProject(id)
      })

      // 直接从本地状态移除
      setAllProjects(prev => prev.filter(p => !successfullyDeletedIds.includes(p.id)))

      // 清空选中状态
      dispatch({ type: 'CLEAR_SELECTION' })
    } catch {
      // 错误提示由拦截器统一处理
    } finally {
      dispatch({ type: 'SET_LOADING', payload: false })
      dispatch({ type: 'HIDE_BATCH_DELETE_MODAL' })
    }
  }

  // 项目创建成功处理
  const handleCreateSuccess = (_project: unknown) => {
    dispatch({ type: 'TOGGLE_CREATE_DRAWER', payload: false })
    // 重新加载数据
    fetchAllProjects()
  }

  // 项目编辑成功处理
  const handleEditSuccess = () => {
    dispatch({ type: 'TOGGLE_EDIT_DRAWER', payload: false })
    dispatch({ type: 'SET_CURRENT_EDIT_PROJECT', payload: null })
    // 重新加载数据
    fetchAllProjects()
  }

  // 表格列配置
  const columns = useMemo<ColumnsType<Project>>(() => [
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
      width: 90,
      render: (type: string) => (
        <Tag color={getProjectTypeColor(type as ProjectType)}>
          {getProjectTypeText(type as ProjectType)}
        </Tag>
      )
    },
    {
      title: '状态',
      dataIndex: 'status',
      key: 'status',
      width: 90,
      render: (status: string) => (
        <Tag color={getProjectStatusColor(status)}>
          {getProjectStatusText(status)}
        </Tag>
      )
    },
    {
      title: '任务数',
      dataIndex: 'task_count',
      key: 'task_count',
      width: 70,
      align: 'center',
      responsive: ['md'],
      render: (count: number) => <span>{count || 0}</span>
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
      )
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
      )
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
      )
    }
  ], [handleDelete, handleEdit, navigate])

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
    <div style={{ padding: '24px' }}>
      {/* 页面标题 */}
      <div style={{ marginBottom: '24px' }}>
        <Space align="start">
          <h1 style={{ fontSize: '24px', fontWeight: 'bold', margin: 0, display: 'flex', alignItems: 'center', gap: '8px' }}>
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
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', flexWrap: 'wrap', gap: '12px' }}>
          <Space wrap size="middle">
            <Button
              icon={<ReloadOutlined />}
              onClick={() => fetchAllProjects()}
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
              icon={<UploadOutlined />}
              onClick={openImportModal}
            >
              导入项目
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
              <Option value="active">活跃</Option>
              <Option value="inactive">非活跃</Option>
              <Option value="error">错误</Option>
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
                options={userList.map((userItem) => ({ value: userItem.id, label: userItem.username }))}
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
              value={searchQuery}
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
        dataSource={filteredAndPaginatedProjects.data}
        rowKey="id"
        loading={uiState.loading}
        minWidth={900}
        fixedActions={true}
        rowSelection={{
          selectedRowKeys: uiState.selectedRowKeys,
          onChange: (newSelectedRowKeys: React.Key[], newSelectedRows: Project[]) => {
            const resolvedProjects = resolveSelectedProjects(newSelectedRowKeys, newSelectedRows)
            dispatch({ 
              type: 'SET_SELECTED_PROJECTS', 
              payload: { keys: newSelectedRowKeys, projects: resolvedProjects } 
            })
          },
          getCheckboxProps: (record: Project) => ({
            name: record.name,
          }),
        }}
        pagination={{
          current: currentPage,
          pageSize: pageSize,
          total: filteredAndPaginatedProjects.total,
          showSizeChanger: true,
          showQuickJumper: true,
          showTotal: (total, range) =>
            `第 ${range[0]}-${range[1]} 条，共 ${total} 条记录`,
          pageSizeOptions: ['10', '20', '50', '100'],
          onChange: (page, size) => {
            handlePaginationChange(page, size || pageSize)
          },
          onShowSizeChange: (_, size) => {
            handlePaginationChange(1, size)
          }
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
          <p>确定要删除选中的 {uiState.selectedRowKeys.length} 个项目吗？此操作不可恢复。</p>
          <div style={{ marginTop: 8 }}>
            <strong>将要删除的项目：</strong>
            <ul style={{ marginTop: 4, marginBottom: 0 }}>
              {uiState.selectedProjects.slice(0, 5).map(project => (
                <li key={project.id}>{project.name}</li>
              ))}
              {uiState.selectedRowKeys.length > 5 && (
                <li>... 还有 {uiState.selectedRowKeys.length - 5} 个项目</li>
              )}
            </ul>
          </div>
        </div>
      </Modal>

      <Modal
        title="导入项目"
        open={importModalVisible}
        onOk={handleImportSubmit}
        onCancel={() => {
          setImportModalVisible(false)
          resetImportState()
        }}
        okText="开始导入"
        cancelText="取消"
        confirmLoading={importSubmitting}
        destroyOnClose
      >
        <Form layout="vertical">
          <Form.Item label="项目文件/压缩包" required>
            <Upload
              fileList={importProjectFiles}
              beforeUpload={(file) => {
                const allowedTypes = ['.py', '.zip', '.tar.gz', '.tar']
                const filename = file.name.toLowerCase()
                const isAllowed = allowedTypes.some(type => filename.endsWith(type))
                if (!isAllowed) {
                  showNotification('error', '只支持 .py、.zip、.tar.gz、.tar 格式的文件')
                  return Upload.LIST_IGNORE
                }
                return false
              }}
              maxCount={1}
              accept=".py,.zip,.tar,.tar.gz"
              onChange={({ fileList }) => setImportProjectFiles(fileList.slice(-1))}
              onRemove={() => {
                setImportProjectFiles([])
                return true
              }}
            >
              <Button icon={<UploadOutlined />}>选择项目文件</Button>
            </Upload>
            <Text type="secondary">
              支持 .py、.zip、.tar.gz、.tar
            </Text>
          </Form.Item>
          <EnvSelector
            value={importEnvConfig}
            onChange={setImportEnvConfig}
            workerList={workers}
          />
          <Form.Item label="项目名称（可选）">
            <Input
              placeholder="默认使用文件名"
              value={importProjectName}
              onChange={(e) => setImportProjectName(e.target.value)}
            />
          </Form.Item>
          <Form.Item label="入口文件（压缩包必填）">
            <Input
              placeholder="压缩包内入口路径，例如 main.py"
              value={importEntryPoint}
              onChange={(e) => setImportEntryPoint(e.target.value)}
            />
            <Text type="secondary">
              压缩包必须填写入口文件；单文件项目可留空自动使用文件名
            </Text>
          </Form.Item>
          <Form.Item>
            <Space direction="vertical">
              <Checkbox
                checked={importOverwriteExisting}
                onChange={(e) => setImportOverwriteExisting(e.target.checked)}
              >
                覆盖同名项目
              </Checkbox>
            </Space>
          </Form.Item>
        </Form>
      </Modal>
    </div>
  )
}

export default ProjectList
