import React, { useEffect, useState, useMemo, useCallback } from 'react'
import {
  Card,
  Space,
  Tag,
  Input,
  Select,
  Button,
  Typography,
  Modal,
  List,
  Tabs,
  Tooltip,
  theme,
  Popconfirm,
  Spin,
  Empty,
  App,
} from 'antd'
import {
  CloudServerOutlined,
  DesktopOutlined,
  EyeOutlined,
  EditOutlined,
  DownloadOutlined,
  DeleteOutlined,
  ReloadOutlined,
} from '@ant-design/icons'
import CopyableTooltip from '@/components/common/CopyableTooltip'
import envService from '@/services/envs'
import {
  getSourceDisplay,
  getScopeDisplay,
  interpreterSourceOptions,
} from '@/config/displayConfig'
import nodeService from '@/services/nodes'
import { useNodeStore } from '@/stores/nodeStore'
import ResponsiveTable from '@/components/common/ResponsiveTable'
import type { Node } from '@/types'
import {
  CreateVenvDrawer,
  InstallPackagesButton,
  EditVenvKeyModal,
  InstallPackagesModal,
  InterpreterDrawer,
} from './components'
import type {
  ExtendedVenvItem,
  PackageModalState,
  EditModalState,
  InstallModalState,
  InterpreterInfo,
} from './types'

const { Search } = Input
const { Text } = Typography

const EnvListPage: React.FC = () => {
  const { token } = theme.useToken()
  const { message, modal } = App.useApp()
  const { currentNode } = useNodeStore()
  const [loading, setLoading] = useState(false)

  // 节点列表
  const [nodes, setNodes] = useState<Node[]>([])

  // 所有环境数据
  const [allItems, setAllItems] = useState<ExtendedVenvItem[]>([])

  // 筛选条件
  const [searchQuery, setSearchQuery] = useState('')
  const [scopeFilter, setScopeFilter] = useState<string | undefined>(undefined)
  const [interpreterSourceFilter, setInterpreterSourceFilter] = useState<string | undefined>(
    undefined
  )
  const [nodeFilter, setNodeFilter] = useState<string | undefined>(undefined)

  // 分页
  const [currentPage, setCurrentPage] = useState(1)
  const [pageSize, setPageSize] = useState(20)

  // 模态框状态
  const [pkgModal, setPkgModal] = useState<PackageModalState>({ open: false })
  const [selectedRowKeys, setSelectedRowKeys] = useState<React.Key[]>([])
  const [editModal, setEditModal] = useState<EditModalState>({ open: false })
  const [installModal, setInstallModal] = useState<InstallModalState>({ open: false })

  // Tab 和解释器
  const [activeTab, setActiveTab] = useState<string>('venvs')
  const [installed, setInstalled] = useState<InterpreterInfo[]>([])
  const [ivLoading, setIvLoading] = useState(false)

  // 加载节点列表
  useEffect(() => {
    nodeService
      .getAllNodes()
      .then(setNodes)
      .catch(() => setNodes([]))
  }, [])

  // 卸载包
  const handleUninstallPackage = async (pkg: { name: string; version: string }) => {
    const { venv } = pkgModal
    if (!venv) return

    try {
      if (venv.isLocal) {
        message.warning('本地环境卸载功能即将支持')
        return
      } else if (venv.nodeId && venv.envName) {
        await envService.uninstallNodeEnvPackages(venv.nodeId, venv.envName, [pkg.name])
        message.success(`已卸载 ${pkg.name}`)

        setPkgModal({ ...pkgModal, loading: true })
        try {
          const pkgs = await envService.listNodeEnvPackages(venv.nodeId, venv.envName)
          setPkgModal({ open: true, venv, packages: pkgs, loading: false })
        } catch (error: unknown) {
          const errMsg = error instanceof Error ? error.message : '刷新依赖列表失败'
          message.error(errMsg)
          setPkgModal({ ...pkgModal, loading: false })
        }
      }
    } catch (error: unknown) {
      const errMsg = error instanceof Error ? error.message : '卸载包失败'
      message.error(errMsg)
    }
  }

  // 加载环境数据
  const fetchAllEnvs = useCallback(async () => {
    setLoading(true)
    try {
      const allEnvs: ExtendedVenvItem[] = []

      if (currentNode) {
        try {
          const nodeEnvs = await envService.listNodeEnvs(currentNode.id)
          const extendedEnvs: ExtendedVenvItem[] = nodeEnvs.map(
            (env): ExtendedVenvItem => ({
              id: `${currentNode.id}-${env.name}`,
              scope: 'private',
              key: env.name,
              version: env.python_version,
              venv_path: env.path,
              interpreter_version: env.python_version,
              interpreter_source: 'local',
              python_bin: env.python_bin,
              install_dir: '',
              created_at: env.created_at,
              isLocal: false,
              nodeName: currentNode.name,
              nodeId: currentNode.id,
              envName: env.name,
            })
          )
          allEnvs.push(...extendedEnvs)
        } catch (error) {
          console.error(`加载节点 ${currentNode.name} 环境失败:`, error)
        }
      } else {
        // 获取本地环境
        try {
          const localResponse = await envService.listVenvs({ page: 1, size: 1000 })
          const localEnvs: ExtendedVenvItem[] = localResponse.items.map((item) => ({
            ...item,
            isLocal: true,
            nodeName: '本地',
            nodeId: undefined,
          }))
          allEnvs.push(...localEnvs)
        } catch (error) {
          console.error('加载本地环境失败:', error)
        }

        // 获取所有在线节点的环境
        const onlineNodes = nodes.filter((n) => n.status === 'online')
        const nodeEnvPromises = onlineNodes.map(async (node) => {
          try {
            const nodeEnvs = await envService.listNodeEnvs(node.id)
            return nodeEnvs.map(
              (env): ExtendedVenvItem => ({
                id: `${node.id}-${env.name}`,
                scope: 'private',
                key: env.name,
                version: env.python_version,
                venv_path: env.path,
                interpreter_version: env.python_version,
                interpreter_source: 'local',
                python_bin: env.python_bin,
                install_dir: '',
                created_at: env.created_at,
                isLocal: false,
                nodeName: node.name,
                nodeId: node.id,
                envName: env.name,
              })
            )
          } catch (error) {
            console.error(`加载节点 ${node.name} 环境失败:`, error)
            return []
          }
        })

        const nodeEnvsResults = await Promise.all(nodeEnvPromises)
        nodeEnvsResults.forEach((envs) => allEnvs.push(...envs))
      }

      setAllItems(allEnvs)
    } catch (error) {
      console.error('加载环境列表失败:', error)
      setAllItems([])
    } finally {
      setLoading(false)
    }
  }, [nodes, currentNode])

  // 筛选和分页
  const filteredAndPaginatedItems = useMemo(() => {
    let filtered = [...allItems]

    if (scopeFilter) {
      filtered = filtered.filter((item) => item.scope === scopeFilter)
    }

    if (interpreterSourceFilter) {
      filtered = filtered.filter((item) => item.interpreter_source === interpreterSourceFilter)
    }

    if (!currentNode && nodeFilter) {
      if (nodeFilter === 'local') {
        filtered = filtered.filter((item) => item.isLocal)
      } else {
        filtered = filtered.filter((item) => item.nodeId === nodeFilter)
      }
    }

    if (searchQuery) {
      const lowerQuery = searchQuery.toLowerCase().trim()
      filtered = filtered.filter((item) => {
        return (
          item.venv_path?.toLowerCase().includes(lowerQuery) ||
          item.key?.toLowerCase().includes(lowerQuery) ||
          item.version?.toLowerCase().includes(lowerQuery) ||
          item.nodeName?.toLowerCase().includes(lowerQuery)
        )
      })
    }

    const total = filtered.length
    const startIndex = (currentPage - 1) * pageSize
    const endIndex = startIndex + pageSize
    const paginatedData = filtered.slice(startIndex, endIndex)

    return { data: paginatedData, total }
  }, [
    allItems,
    scopeFilter,
    interpreterSourceFilter,
    nodeFilter,
    searchQuery,
    currentPage,
    pageSize,
    currentNode,
  ])

  // 监听节点切换
  useEffect(() => {
    if (nodes.length > 0) {
      setNodeFilter(undefined)
      setCurrentPage(1)
      fetchAllEnvs()
    }
  }, [fetchAllEnvs, nodes.length, currentNode?.id])

  // 筛选变化处理
  const handleSearchInput = (e: React.ChangeEvent<HTMLInputElement>) => {
    setSearchQuery(e.target.value)
    setCurrentPage(1)
  }

  const handlePaginationChange = (page: number, size: number) => {
    setCurrentPage(page)
    if (size !== pageSize) {
      setPageSize(size)
      setCurrentPage(1)
    }
  }

  // 刷新解释器
  const refreshInterpreters = async () => {
    setIvLoading(true)
    setInstalled([])
    try {
      if (currentNode) {
        const nodeInterpreters = await envService.listNodeInterpreters(currentNode.id)
        setInstalled(
          nodeInterpreters.interpreters.map((interp) => ({
            version: interp.version,
            python_bin: interp.python_bin,
            install_dir: interp.install_dir || '',
            source: interp.source,
            nodeName: currentNode.name,
            nodeId: currentNode.id,
          }))
        )
      } else {
        const allInterpreters: InterpreterInfo[] = []

        const [, localIns] = await Promise.all([
          envService.listPythonVersions(),
          envService.listInterpreters(),
        ])
        allInterpreters.push(
          ...localIns.map((interp) => ({
            ...interp,
            nodeName: '本地',
            nodeId: undefined,
          }))
        )

        const onlineNodes = nodes.filter((n) => n.status === 'online')
        const nodeInterpreterPromises = onlineNodes.map(async (node) => {
          try {
            const nodeInterpreters = await envService.listNodeInterpreters(node.id)
            return nodeInterpreters.interpreters.map((interp) => ({
              version: interp.version,
              python_bin: interp.python_bin,
              install_dir: interp.install_dir || '',
              source: interp.source,
              nodeName: node.name,
              nodeId: node.id,
            }))
          } catch (error) {
            console.error(`获取节点 ${node.name} 解释器失败:`, error)
            return []
          }
        })

        const nodeInterpretersResults = await Promise.all(nodeInterpreterPromises)
        nodeInterpretersResults.forEach((interpreters) => allInterpreters.push(...interpreters))

        setInstalled(allInterpreters)
      }
    } finally {
      setIvLoading(false)
    }
  }

  useEffect(() => {
    if (activeTab === 'interpreters') {
      refreshInterpreters()
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [activeTab, currentNode?.id, nodes.length])

  // 节点筛选选项
  const nodeFilterOptions = useMemo(() => {
    return [
      { value: 'local', label: '本地' },
      ...nodes.filter((n) => n.status === 'online').map((node) => ({
        value: node.id,
        label: node.name,
      })),
    ]
  }, [nodes])


  // 表格列定义
  const columns = [
    {
      title: '节点',
      dataIndex: 'nodeName',
      key: 'nodeName',
      width: 130,
      ellipsis: true,
      render: (_: unknown, record: ExtendedVenvItem) => {
        const nodeName = record.nodeName || '未知'
        const icon = record.isLocal ? (
          <DesktopOutlined style={{ fontSize: 12 }} />
        ) : (
          <CloudServerOutlined style={{ fontSize: 12 }} />
        )
        return (
          <Tooltip title={nodeName} placement="topLeft">
            <Tag
              color={record.isLocal ? 'geekblue' : 'cyan'}
              style={{
                maxWidth: '100%',
                overflow: 'hidden',
                textOverflow: 'ellipsis',
                display: 'inline-flex',
                alignItems: 'center',
                gap: '4px',
              }}
            >
              {icon}
              <span style={{ overflow: 'hidden', textOverflow: 'ellipsis' }}>{nodeName}</span>
            </Tag>
          </Tooltip>
        )
      },
    },
    {
      title: '作用域',
      dataIndex: 'scope',
      key: 'scope',
      width: 80,
      render: (v: string) => {
        const display = getScopeDisplay(v)
        return <Tag color={display.color}>{display.label}</Tag>
      },
    },
    {
      title: '标识',
      dataIndex: 'key',
      key: 'key',
      width: 130,
      ellipsis: true,
      render: (v?: string) =>
        v ? (
          <Tooltip title={v} placement="topLeft">
            <span>{v}</span>
          </Tooltip>
        ) : (
          '-'
        ),
    },
    {
      title: 'Python',
      dataIndex: 'version',
      key: 'version',
      width: 100,
      render: (v: string) => <Tag color="blue">{v}</Tag>,
    },
    {
      title: '路径',
      dataIndex: 'venv_path',
      key: 'venv_path',
      ellipsis: true,
      render: (v: string) => (
        <CopyableTooltip text={v}>
          <span style={{ cursor: 'pointer' }}>{v}</span>
        </CopyableTooltip>
      ),
    },
    {
      title: '创建人',
      dataIndex: 'created_by_username',
      key: 'created_by_username',
      width: 90,
      render: (_: unknown, r: ExtendedVenvItem) => r.created_by_username || '-',
    },
    {
      title: '创建时间',
      dataIndex: 'created_at',
      key: 'created_at',
      width: 100,
      render: (v?: string) => {
        if (!v) return '-'
        const date = v.split('T')[0]
        const time = v.split('T')[1]?.split('.')[0] || ''
        return (
          <Tooltip title={`${date} ${time}`} placement="topLeft">
            <span>{date}</span>
          </Tooltip>
        )
      },
    },
  ]

  // 操作列渲染
  const renderActions = (_: unknown, record: ExtendedVenvItem) => {
    // 节点环境的操作
    if (!record.isLocal && record.nodeId) {
      return (
        <Space size="small">
          <Tooltip title="查看依赖" placement="top">
            <Button
              type="link"
              size="small"
              icon={<EyeOutlined />}
              onClick={async () => {
                try {
                  const pkgs = await envService.listNodeEnvPackages(
                    record.nodeId!,
                    record.envName || record.key || ''
                  )
                  setPkgModal({ open: true, venv: record, packages: pkgs })
                } catch (error: unknown) {
                  const errMsg = error instanceof Error ? error.message : '获取依赖列表失败'
                  message.error(errMsg)
                }
              }}
            />
          </Tooltip>
          <Tooltip title="编辑环境" placement="top">
            <Button
              type="link"
              size="small"
              icon={<EditOutlined />}
              onClick={() => setEditModal({ open: true, venv: record })}
            />
          </Tooltip>
          <Tooltip title="安装依赖" placement="top">
            <Button
              type="link"
              size="small"
              icon={<DownloadOutlined />}
              onClick={() =>
                setInstallModal({
                  open: true,
                  venvId: record.id,
                })
              }
            />
          </Tooltip>
          <Tooltip title="删除" placement="top">
            <Button
              type="link"
              size="small"
              danger
              icon={<DeleteOutlined />}
              onClick={async () => {
                modal.confirm({
                  title: '确认删除节点环境？',
                  content: `将从节点 ${record.nodeName} 删除环境 ${record.key || record.envName}`,
                  onOk: async () => {
                    try {
                      await envService.deleteNodeEnv(
                        record.nodeId!,
                        record.envName || record.key || ''
                      )
                      message.success('环境删除成功')
                      fetchAllEnvs()
                    } catch (error: unknown) {
                      const errMsg = error instanceof Error ? error.message : '删除环境失败'
                      message.error(errMsg)
                    }
                  },
                })
              }}
            />
          </Tooltip>
        </Space>
      )
    }

    // 本地环境的操作
    return (
      <Space size="small">
        <Tooltip title="查看依赖" placement="top">
          <Button
            type="link"
            size="small"
            icon={<EyeOutlined />}
            onClick={async () => {
              const pkgs = await envService.listVenvPackagesById(record.id)
              setPkgModal({ open: true, venv: record, packages: pkgs })
            }}
          />
        </Tooltip>
        <Tooltip
          title={record.scope === 'shared' ? '编辑标识' : '私有环境不支持编辑'}
          placement="top"
        >
          <Button
            type="link"
            size="small"
            icon={<EditOutlined />}
            disabled={record.scope !== 'shared'}
            onClick={() => {
              if (record.scope === 'shared') {
                setEditModal({ open: true, venv: record })
              }
            }}
          />
        </Tooltip>
        <Tooltip title="安装依赖" placement="top">
          <Button
            type="link"
            size="small"
            icon={<DownloadOutlined />}
            onClick={() =>
              setInstallModal({
                open: true,
                venvId: record.id,
              })
            }
          />
        </Tooltip>
        <Tooltip title="删除" placement="top">
          <Button
            type="link"
            size="small"
            danger
            icon={<DeleteOutlined />}
            onClick={async () => {
              if (record.scope === 'shared') {
                modal.confirm({
                  title: '确认删除共享环境？',
                  content: '仅未被项目使用的共享环境可删除',
                  onOk: async () => {
                    try {
                      await envService.deleteVenv(record.id)
                      message.success('删除成功')
                      fetchAllEnvs()
                    } catch (error: unknown) {
                      const errObj = error as {
                        response?: { data?: { detail?: string } }
                        message?: string
                      }
                      const errMsg =
                        errObj?.response?.data?.detail || errObj?.message || '删除失败'
                      message.error(errMsg)
                    }
                  },
                })
              } else {
                modal.confirm({
                  title: '确认删除私有环境？',
                  content: record.current_project_id
                    ? `该操作会解除项目(${record.current_project_id})的环境绑定`
                    : '该操作将删除该私有环境',
                  onOk: async () => {
                    try {
                      await envService.deleteVenv(record.id, true)
                      message.success('删除成功')
                      fetchAllEnvs()
                    } catch (error: unknown) {
                      const errObj = error as {
                        response?: { data?: { detail?: string } }
                        message?: string
                      }
                      const errMsg =
                        errObj?.response?.data?.detail || errObj?.message || '删除失败'
                      message.error(errMsg)
                    }
                  },
                })
              }
            }}
          />
        </Tooltip>
      </Space>
    )
  }


  // 解释器表格列
  const interpreterColumns = [
    {
      title: '节点',
      dataIndex: 'nodeName',
      width: 120,
      ellipsis: true,
      render: (nodeName?: string) => {
        const displayName = nodeName || '未知'
        const isLocal = displayName === '本地'
        const icon = isLocal ? (
          <DesktopOutlined style={{ fontSize: 12 }} />
        ) : (
          <CloudServerOutlined style={{ fontSize: 12 }} />
        )
        return (
          <Tooltip title={displayName} placement="topLeft">
            <Tag
              color={isLocal ? 'geekblue' : 'cyan'}
              style={{
                maxWidth: '100%',
                overflow: 'hidden',
                textOverflow: 'ellipsis',
                display: 'inline-flex',
                alignItems: 'center',
                gap: '4px',
              }}
            >
              {icon}
              <span style={{ overflow: 'hidden', textOverflow: 'ellipsis' }}>{displayName}</span>
            </Tag>
          </Tooltip>
        )
      },
    },
    {
      title: '版本',
      dataIndex: 'version',
      width: 100,
      render: (v: string) => <Tag color="blue">{v}</Tag>,
    },
    {
      title: '来源',
      dataIndex: 'source',
      width: 100,
      render: (v: string) => {
        const display = getSourceDisplay(v)
        return <Tag color={display.color}>{display.label || v}</Tag>
      },
    },
    {
      title: '可执行文件',
      dataIndex: 'python_bin',
      ellipsis: true,
      render: (v: string) => (
        <CopyableTooltip text={v}>
          <span style={{ cursor: 'pointer' }}>{v}</span>
        </CopyableTooltip>
      ),
    },
    {
      title: '安装目录',
      dataIndex: 'install_dir',
      width: 180,
      ellipsis: true,
      render: (v: string) =>
        v ? (
          <CopyableTooltip text={v}>
            <span style={{ cursor: 'pointer' }}>{v}</span>
          </CopyableTooltip>
        ) : (
          <Text type="secondary">-</Text>
        ),
    },
    {
      title: '操作',
      key: 'actions',
      width: 80,
      fixed: 'right' as const,
      render: (_: unknown, r: InterpreterInfo) => (
        <Tooltip title={r.source === 'local' ? '删除记录' : '卸载解释器'} placement="top">
          <Button
            type="link"
            danger
            size="small"
            onClick={async () => {
              modal.confirm({
                title: r.source === 'local' ? '确认删除记录？' : '确认卸载解释器？',
                content:
                  r.source === 'local'
                    ? '将从记录中移除该本地解释器'
                    : `将卸载 mise 管理的 Python ${r.version}`,
                onOk: async () => {
                  if (currentNode) {
                    if (r.source === 'system') {
                      message.warning('系统解释器不可卸载')
                      return
                    }
                    await envService.unregisterNodeInterpreter(
                      currentNode.id,
                      r.version,
                      r.source || 'local'
                    )
                  } else {
                    await envService.uninstallInterpreter(r.version, r.source || 'mise')
                  }
                  refreshInterpreters()
                },
              })
            }}
          >
            {r.source === 'local' ? '删除' : '卸载'}
          </Button>
        </Tooltip>
      ),
    },
  ]

  return (
    <div style={{ padding: '24px' }}>
      {/* 页面头部 */}
      <div
        style={{
          display: 'flex',
          justifyContent: 'space-between',
          alignItems: 'center',
          marginBottom: 16,
          flexWrap: 'wrap',
          gap: 12,
        }}
      >
        <Space>
          <h1
            style={{
              fontSize: '20px',
              fontWeight: 600,
              margin: 0,
              display: 'flex',
              alignItems: 'center',
              gap: 8,
            }}
          >
            <CloudServerOutlined style={{ color: token.colorPrimary }} />
            环境管理
          </h1>
          {currentNode && (
            <Tag color="cyan" style={{ display: 'inline-flex', alignItems: 'center', gap: '4px' }}>
              <CloudServerOutlined style={{ fontSize: 12 }} />
              <span>{currentNode.name}</span>
            </Tag>
          )}
        </Space>
      </div>

      <Card>
        {/* 工具栏 */}
        <div
          style={{
            display: 'flex',
            justifyContent: 'space-between',
            alignItems: 'center',
            flexWrap: 'wrap',
            gap: 12,
            marginBottom: 16,
            paddingBottom: 16,
            borderBottom: `1px solid ${token.colorBorderSecondary}`,
          }}
        >
          <Space wrap size="small">
            <Button
              icon={<ReloadOutlined />}
              onClick={() => (activeTab === 'venvs' ? fetchAllEnvs() : refreshInterpreters())}
              loading={activeTab === 'venvs' ? loading : ivLoading}
              size="small"
            >
              刷新
            </Button>
            {activeTab === 'venvs' && (
              <>
                <CreateVenvDrawer onCreated={fetchAllEnvs} />
                <Button
                  danger
                  disabled={!selectedRowKeys.length}
                  icon={<DeleteOutlined />}
                  size="small"
                  onClick={async () => {
                    modal.confirm({
                      title: '确定要删除选中的共享环境吗？',
                      content: '该操作不可恢复，且仅对未被项目绑定的共享环境生效。',
                      onOk: async () => {
                        await envService.batchDeleteVenvs(selectedRowKeys as string[])
                        setSelectedRowKeys([])
                        fetchAllEnvs()
                      },
                    })
                  }}
                >
                  批量删除 {selectedRowKeys.length > 0 && `(${selectedRowKeys.length})`}
                </Button>
                <InstallPackagesButton
                  venvId=""
                  onInstalled={() => {}}
                  batch
                  selectedIds={selectedRowKeys as string[]}
                />
              </>
            )}
            {activeTab === 'interpreters' && (
              <InterpreterDrawer
                onAdded={refreshInterpreters}
                currentNode={currentNode || undefined}
              />
            )}
          </Space>
          {activeTab === 'venvs' && (
            <Space wrap size="small">
              {!currentNode && (
                <Select
                  allowClear
                  placeholder="节点"
                  style={{ width: 120 }}
                  value={nodeFilter}
                  onChange={(v) => {
                    setNodeFilter(v)
                    setCurrentPage(1)
                  }}
                  options={nodeFilterOptions}
                  size="small"
                />
              )}
              <Select
                allowClear
                placeholder="作用域"
                style={{ width: 90 }}
                value={scopeFilter}
                onChange={(v) => {
                  setScopeFilter(v)
                  setCurrentPage(1)
                }}
                options={[
                  { value: 'private', label: '私有' },
                  { value: 'shared', label: '公共' },
                ]}
                size="small"
              />
              <Select
                allowClear
                placeholder="解释器来源"
                style={{ width: 110 }}
                value={interpreterSourceFilter}
                onChange={(v) => {
                  setInterpreterSourceFilter(v)
                  setCurrentPage(1)
                }}
                options={interpreterSourceOptions.map((opt) => ({
                  value: opt.value,
                  label: opt.value,
                }))}
                size="small"
              />
              <Search
                placeholder={currentNode ? '搜索路径/标识/版本' : '搜索路径/标识/版本/节点'}
                value={searchQuery}
                onChange={handleSearchInput}
                onSearch={(v) => {
                  setSearchQuery(v)
                  setCurrentPage(1)
                }}
                allowClear
                style={{ width: 200 }}
                size="small"
              />
            </Space>
          )}
        </div>

        {/* Tabs */}
        <Tabs
          activeKey={activeTab}
          onChange={setActiveTab}
          size="small"
          items={[
            {
              key: 'venvs',
              label: '虚拟环境',
              children: (
                <ResponsiveTable
                  rowKey="id"
                  loading={loading}
                  rowSelection={{ selectedRowKeys, onChange: setSelectedRowKeys }}
                  columns={[
                    ...columns,
                    {
                      title: '操作',
                      key: 'actions',
                      width: 160,
                      fixed: 'right' as const,
                      render: renderActions,
                    },
                  ]}
                  dataSource={filteredAndPaginatedItems.data}
                  minWidth={1000}
                  fixedActions={true}
                  pagination={{
                    current: currentPage,
                    pageSize: pageSize,
                    total: filteredAndPaginatedItems.total,
                    onChange: handlePaginationChange,
                    onShowSizeChange: (_, size) => handlePaginationChange(1, size),
                    showSizeChanger: true,
                    showQuickJumper: true,
                    showTotal: (total, range) =>
                      `第 ${range[0]}-${range[1]} 条，共 ${total} 条记录`,
                    pageSizeOptions: ['10', '20', '50', '100'],
                  }}
                  size="middle"
                />
              ),
            },
            {
              key: 'interpreters',
              label: '解释器',
              children: (
                <ResponsiveTable
                  rowKey={(r) => `${r.version || ''}-${r.python_bin || ''}-${r.nodeId || 'local'}`}
                  loading={ivLoading}
                  columns={interpreterColumns}
                  dataSource={installed}
                  minWidth={800}
                  fixedActions={true}
                  pagination={false}
                  size="middle"
                />
              ),
            },
          ]}
        />
      </Card>

      {/* 依赖列表 Modal */}
      <Modal
        open={pkgModal.open}
        onCancel={() => setPkgModal({ open: false })}
        title={`依赖列表 - ${pkgModal.venv?.key || pkgModal.venv?.version || ''}`}
        footer={[
          <Button key="close" onClick={() => setPkgModal({ open: false })}>
            关闭
          </Button>,
          !pkgModal.venv?.isLocal && (
            <Button
              key="install"
              type="primary"
              icon={<DownloadOutlined />}
              onClick={() => {
                setPkgModal({ open: false })
                setInstallModal({
                  open: true,
                  venvId: typeof pkgModal.venv?.id === 'number' ? pkgModal.venv.id : undefined,
                })
              }}
            >
              安装包
            </Button>
          ),
        ]}
        width={700}
      >
        <Spin spinning={pkgModal.loading || false}>
          {(pkgModal.packages || []).length > 0 ? (
            <List
              dataSource={pkgModal.packages || []}
              renderItem={(item: { name: string; version: string }) => (
                <List.Item
                  actions={
                    !pkgModal.venv?.isLocal
                      ? [
                          <Popconfirm
                            key="uninstall"
                            title="确定要卸载此包吗？"
                            onConfirm={() => handleUninstallPackage(item)}
                            okText="确定"
                            cancelText="取消"
                          >
                            <Button type="link" size="small" danger>
                              卸载
                            </Button>
                          </Popconfirm>,
                        ]
                      : []
                  }
                >
                  <List.Item.Meta
                    title={<strong>{item.name}</strong>}
                    description={`版本: ${item.version}`}
                  />
                </List.Item>
              )}
            />
          ) : (
            <Empty description="暂无已安装的包" />
          )}
        </Spin>
      </Modal>

      {/* 编辑标识 Modal */}
      <EditVenvKeyModal
        open={editModal.open}
        venv={editModal.venv}
        onClose={() => setEditModal({ open: false })}
        onSuccess={() => {
          setEditModal({ open: false })
          fetchAllEnvs()
        }}
      />

      {/* 安装依赖 Modal */}
      <InstallPackagesModal
        open={installModal.open}
        venvId={installModal.venvId}
        onClose={() => setInstallModal({ open: false })}
        onSuccess={() => {
          setInstallModal({ open: false })
          fetchAllEnvs()
        }}
      />
    </div>
  )
}

export default EnvListPage
