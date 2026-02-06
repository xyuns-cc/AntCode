import React, { useCallback, useEffect, useMemo, useState } from 'react'
import {
  App,
  Button,
  Card,
  Empty,
  Input,
  List,
  Modal,
  Popconfirm,
  Select,
  Space,
  Spin,
  Tabs,
  Tag,
  Tooltip,
  Typography,
  theme,
} from 'antd'
import {
  CloudServerOutlined,
  DesktopOutlined,
  DeleteOutlined,
  DownloadOutlined,
  EditOutlined,
  EyeOutlined,
  ReloadOutlined,
} from '@ant-design/icons'
import CopyableTooltip from '@/components/common/CopyableTooltip'
import ResponsiveTable from '@/components/common/ResponsiveTable'
import envService from '@/services/envs'
import { runtimeService, type RuntimeEnv } from '@/services/runtimes'
import { useWorkerStore } from '@/stores/workerStore'
import {
  getScopeDisplay,
  getSourceDisplay,
  interpreterSourceOptions,
} from '@/config/displayConfig'
import type { Worker } from '@/types'
import {
  CreateVenvDrawer,
  EditVenvKeyModal,
  InstallPackagesButton,
  InstallPackagesModal,
  InterpreterDrawer,
} from './components'
import type {
  EditModalState,
  ExtendedVenvItem,
  InstallModalState,
  InterpreterInfo,
  PackageModalState,
} from './types'

const { Search } = Input
const { Text } = Typography

const buildWorkerEnvId = (workerId: string, envName: string) => `${workerId}|${envName}`

const toWorkerVenvItem = (worker: Worker, env: RuntimeEnv): ExtendedVenvItem => {
  const scope = env.scope || (env.name?.startsWith('shared-') ? 'shared' : 'private')
  return {
    id: buildWorkerEnvId(worker.id, env.name),
    scope,
    key: env.name,
    version: env.python_version,
    venv_path: env.path,
    interpreter_version: env.python_version,
    interpreter_source: 'local',
    python_bin: env.python_executable,
    install_dir: '',
    created_by_username: env.created_by || null,
    created_at: env.created_at || null,
    updated_at: null,
    current_project_id: null,
    packages: undefined,
    isLocal: false,
    workerName: worker.name,
    workerId: worker.id,
    envName: env.name,
  }
}

const EnvListPage: React.FC = () => {
  const { token } = theme.useToken()
  const { message, modal } = App.useApp()
  const { currentWorker, workers, refreshWorkers } = useWorkerStore()

  const [loading, setLoading] = useState(false)
  const [allItems, setAllItems] = useState<ExtendedVenvItem[]>([])

  const [searchQuery, setSearchQuery] = useState('')
  const [scopeFilter, setScopeFilter] = useState<string | undefined>(undefined)
  const [interpreterSourceFilter, setInterpreterSourceFilter] = useState<string | undefined>(
    undefined
  )
  const [workerFilter, setWorkerFilter] = useState<string | undefined>(undefined)

  const [currentPage, setCurrentPage] = useState(1)
  const [pageSize, setPageSize] = useState(20)

  const [pkgModal, setPkgModal] = useState<PackageModalState>({ open: false })
  const [selectedRowKeys, setSelectedRowKeys] = useState<React.Key[]>([])
  const [editModal, setEditModal] = useState<EditModalState>({ open: false })
  const [installModal, setInstallModal] = useState<InstallModalState>({ open: false })

  const [activeTab, setActiveTab] = useState<string>('venvs')
  const [installed, setInstalled] = useState<InterpreterInfo[]>([])
  const [ivLoading, setIvLoading] = useState(false)

  useEffect(() => {
    if (workers.length === 0) {
      refreshWorkers()
    }
  }, [workers.length, refreshWorkers])

  const fetchAllEnvs = useCallback(async () => {
    setLoading(true)
    try {
      const allEnvs: ExtendedVenvItem[] = []

      if (currentWorker) {
        const envs = await runtimeService.listEnvs(currentWorker.id)
        allEnvs.push(...envs.map((env) => toWorkerVenvItem(currentWorker, env)))
      } else {
        // 本地环境（Web API 所在机器）
        try {
          const localResponse = await envService.listVenvs({ page: 1, size: 1000 })
          allEnvs.push(
            ...localResponse.items.map((item) => ({
              ...item,
              isLocal: true,
              workerName: '本地',
              workerId: undefined,
              envName: undefined,
            }))
          )
        } catch {
          // 本地环境失败时不阻塞 Worker 环境展示
        }

        // 在线 Worker 环境
        const onlineWorkers = workers.filter((w) => w.status === 'online')
        const results = await Promise.all(
          onlineWorkers.map(async (w) => {
            try {
              const envs = await runtimeService.listEnvs(w.id)
              return envs.map((env) => toWorkerVenvItem(w, env))
            } catch {
              return []
            }
          })
        )
        results.forEach((items) => allEnvs.push(...items))
      }

      setAllItems(allEnvs)
    } catch {
      setAllItems([])
    } finally {
      setLoading(false)
    }
  }, [currentWorker, workers])

  useEffect(() => {
    fetchAllEnvs()
  }, [fetchAllEnvs])

  const refreshInterpreters = useCallback(async () => {
    setIvLoading(true)
    setInstalled([])
    try {
      if (currentWorker) {
        const items = await runtimeService.listInterpreters(currentWorker.id)
        setInstalled(
          items.map((interp) => ({
            version: interp.version,
            python_bin: interp.python_bin || '',
            install_dir: interp.install_dir || '',
            source: interp.source,
            workerName: currentWorker.name,
            workerId: currentWorker.id,
          }))
        )
      } else {
        const all: InterpreterInfo[] = []
        const localItems = await envService.listInterpreters()
        all.push(
          ...localItems.map((interp) => ({
            version: interp.version,
            python_bin: interp.python_bin,
            install_dir: interp.install_dir,
            source: interp.source,
            workerName: '本地',
            workerId: undefined,
          }))
        )

        const onlineWorkers = workers.filter((w) => w.status === 'online')
        const results = await Promise.all(
          onlineWorkers.map(async (w) => {
            try {
              const items = await runtimeService.listInterpreters(w.id)
              return items.map((interp) => ({
                version: interp.version,
                python_bin: interp.python_bin || '',
                install_dir: interp.install_dir || '',
                source: interp.source,
                workerName: w.name,
                workerId: w.id,
              }))
            } catch {
              return []
            }
          })
        )
        results.forEach((items) => all.push(...items))

        setInstalled(all)
      }
    } finally {
      setIvLoading(false)
    }
  }, [currentWorker, workers])

  useEffect(() => {
    if (activeTab === 'interpreters') {
      refreshInterpreters()
    }
  }, [activeTab, refreshInterpreters])

  // 筛选和分页
  const filteredAndPaginatedItems = useMemo(() => {
    let filtered = [...allItems]

    if (scopeFilter) {
      filtered = filtered.filter((item) => item.scope === scopeFilter)
    }

    if (interpreterSourceFilter) {
      filtered = filtered.filter((item) => item.interpreter_source === interpreterSourceFilter)
    }

    if (!currentWorker && workerFilter) {
      if (workerFilter === 'local') {
        filtered = filtered.filter((item) => item.isLocal)
      } else {
        filtered = filtered.filter((item) => item.workerId === workerFilter)
      }
    }

    if (searchQuery) {
      const lowerQuery = searchQuery.toLowerCase().trim()
      filtered = filtered.filter((item) => {
        return (
          item.venv_path?.toLowerCase().includes(lowerQuery) ||
          item.key?.toLowerCase().includes(lowerQuery) ||
          item.version?.toLowerCase().includes(lowerQuery) ||
          item.workerName?.toLowerCase().includes(lowerQuery)
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
    workerFilter,
    searchQuery,
    currentPage,
    pageSize,
    currentWorker,
  ])

  const handlePaginationChange = (page: number, size: number) => {
    setCurrentPage(page)
    if (size !== pageSize) {
      setPageSize(size)
      setCurrentPage(1)
    }
  }

  const workerFilterOptions = useMemo(() => {
    return [
      { value: 'local', label: '本地' },
      ...workers
        .filter((w) => w.status === 'online')
        .map((w) => ({
          value: w.id,
          label: w.name,
        })),
    ]
  }, [workers])

  const handleUninstallPackage = async (pkg: { name: string; version: string }) => {
    const { venv } = pkgModal
    if (!venv) return

    try {
      if (venv.isLocal) {
        message.warning('本地环境卸载功能暂不支持')
        return
      }
      if (!venv.workerId || !venv.envName) return

      await runtimeService.uninstallPackages(venv.workerId, venv.envName, [pkg.name])
      message.success(`已卸载 ${pkg.name}`)

      setPkgModal({ ...pkgModal, loading: true })
      const pkgs = await runtimeService.listPackages(venv.workerId, venv.envName)
      setPkgModal({ open: true, venv, packages: pkgs, loading: false })
    } catch (error: unknown) {
      const errMsg = error instanceof Error ? error.message : '卸载包失败'
      message.error(errMsg)
      setPkgModal({ ...pkgModal, loading: false })
    }
  }

  const columns = [
    {
      title: '位置',
      dataIndex: 'workerName',
      key: 'workerName',
      width: 140,
      ellipsis: true,
      render: (_: unknown, record: ExtendedVenvItem) => {
        const name = record.workerName || '未知'
        const icon = record.isLocal ? (
          <DesktopOutlined style={{ fontSize: 12 }} />
        ) : (
          <CloudServerOutlined style={{ fontSize: 12 }} />
        )
        return (
          <Tooltip title={name} placement="topLeft">
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
              <span style={{ overflow: 'hidden', textOverflow: 'ellipsis' }}>{name}</span>
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
      title: '名称',
      dataIndex: 'key',
      key: 'key',
      width: 180,
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
      render: (v?: string | null) => {
        if (!v) return '-'
        const date = String(v).split('T')[0]
        const time = String(v).split('T')[1]?.split('.')[0] || ''
        return (
          <Tooltip title={`${date} ${time}`} placement="topLeft">
            <span>{date}</span>
          </Tooltip>
        )
      },
    },
  ]

  const renderActions = (_: unknown, record: ExtendedVenvItem) => {
    if (!record.isLocal && record.workerId && record.envName) {
      return (
        <Space size="small">
          <Tooltip title="查看依赖" placement="top">
            <Button
              type="link"
              size="small"
              icon={<EyeOutlined />}
              onClick={async () => {
                try {
                  const pkgs = await runtimeService.listPackages(record.workerId!, record.envName!)
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
              onClick={() => setInstallModal({ open: true, venvId: record.id })}
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
                  title: '确认删除 Worker 环境？',
                  content: `将从 Worker ${record.workerName} 删除环境 ${record.envName}`,
                  onOk: async () => {
                    try {
                      await runtimeService.deleteEnv(record.workerId!, record.envName!)
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

    // 本地环境
    return (
      <Space size="small">
        <Tooltip title="查看依赖" placement="top">
          <Button
            type="link"
            size="small"
            icon={<EyeOutlined />}
            onClick={async () => {
              try {
                const pkgs = await envService.listVenvPackagesById(record.id)
                setPkgModal({ open: true, venv: record, packages: pkgs })
              } catch (error: unknown) {
                const errMsg = error instanceof Error ? error.message : '获取依赖列表失败'
                message.error(errMsg)
              }
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
            onClick={() => setEditModal({ open: true, venv: record })}
          />
        </Tooltip>
        <Tooltip title="安装依赖" placement="top">
          <Button
            type="link"
            size="small"
            icon={<DownloadOutlined />}
            onClick={() => setInstallModal({ open: true, venvId: record.id })}
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
                title: '确认删除本地环境？',
                content: `将删除本地环境 ${record.key || record.id}`,
                onOk: async () => {
                  try {
                    await envService.deleteVenv(record.id, record.scope === 'private')
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

  const interpreterColumns = [
    {
      title: '位置',
      dataIndex: 'workerName',
      width: 140,
      ellipsis: true,
      render: (name?: string) => {
        const displayName = name || '未知'
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
      width: 110,
      render: (v: string) => <Tag color="blue">{v}</Tag>,
    },
    {
      title: '来源',
      dataIndex: 'source',
      width: 110,
      render: (v: string) => {
        const display = getSourceDisplay(v)
        return <Tag color={display.color}>{display.label || v}</Tag>
      },
    },
    {
      title: '可执行文件',
      dataIndex: 'python_bin',
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
      width: 90,
      fixed: 'right' as const,
      render: (_: unknown, r: InterpreterInfo) => (
        <Tooltip title={r.source === 'system' ? '系统解释器不可移除' : '移除'} placement="top">
          <Button
            type="link"
            danger
            size="small"
            disabled={r.source === 'system'}
            onClick={async () => {
              modal.confirm({
                title: '确认移除解释器？',
                content: r.workerName ? `将从 ${r.workerName} 移除解释器 ${r.version}` : `移除解释器 ${r.version}`,
                onOk: async () => {
                  try {
                    if (r.workerId) {
                      const mode = r.source === 'local' ? 'unregister' : 'uninstall'
                      await runtimeService.removeInterpreter(r.workerId, {
                        version: r.version,
                        python_bin: r.python_bin,
                        mode,
                      })
                    } else {
                      await envService.uninstallInterpreter(r.version, r.source || 'mise')
                    }
                    refreshInterpreters()
                  } catch (error: unknown) {
                    const errMsg = error instanceof Error ? error.message : '移除失败'
                    message.error(errMsg)
                  }
                },
              })
            }}
          >
            移除
          </Button>
        </Tooltip>
      ),
    },
  ]

  const rowSelection = {
    selectedRowKeys,
    onChange: (keys: React.Key[]) => setSelectedRowKeys(keys),
    getCheckboxProps: (record: ExtendedVenvItem) => ({
      disabled: !record.isLocal || record.scope !== 'shared',
    }),
  }

  return (
    <div style={{ padding: '24px' }}>
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
            运行时管理
          </h1>
          {currentWorker && (
            <Tag color="cyan" style={{ display: 'inline-flex', alignItems: 'center', gap: '4px' }}>
              <CloudServerOutlined style={{ fontSize: 12 }} />
              <span>{currentWorker.name}</span>
            </Tag>
          )}
        </Space>
      </div>

      <Card>
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

            {activeTab === 'venvs' && !currentWorker && (
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
              <InterpreterDrawer onAdded={refreshInterpreters} currentWorker={currentWorker} />
            )}
          </Space>

          {activeTab === 'venvs' && (
            <Space wrap size="small">
              {!currentWorker && (
                <Select
                  allowClear
                  placeholder="Worker"
                  style={{ width: 160 }}
                  value={workerFilter}
                  onChange={(v) => {
                    setWorkerFilter(v)
                    setCurrentPage(1)
                  }}
                  options={workerFilterOptions}
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
                placeholder={currentWorker ? '搜索路径/名称/版本' : '搜索路径/名称/版本/位置'}
                value={searchQuery}
                onChange={(e) => {
                  setSearchQuery(e.target.value)
                  setCurrentPage(1)
                }}
                onSearch={() => setCurrentPage(1)}
                allowClear
                style={{ width: 220 }}
                size="small"
              />
            </Space>
          )}
        </div>

        <Tabs
          activeKey={activeTab}
          onChange={setActiveTab}
          size="small"
          items={[
            {
              key: 'venvs',
              label: '环境',
              children: (
                <ResponsiveTable
                  rowKey="id"
                  loading={loading}
                  rowSelection={activeTab === 'venvs' ? rowSelection : undefined}
                  columns={[
                    ...columns,
                    {
                      title: '操作',
                      key: 'actions',
                      width: 180,
                      fixed: 'right' as const,
                      render: renderActions,
                    },
                  ]}
                  dataSource={filteredAndPaginatedItems.data}
                  minWidth={1050}
                  fixedActions
                  pagination={{
                    current: currentPage,
                    pageSize: pageSize,
                    total: filteredAndPaginatedItems.total,
                    onChange: handlePaginationChange,
                    onShowSizeChange: (_, size) => handlePaginationChange(1, size),
                    showSizeChanger: true,
                    showQuickJumper: true,
                    showTotal: (total, range) => `第 ${range[0]}-${range[1]} 条，共 ${total} 条记录`,
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
                  rowKey={(r: InterpreterInfo) =>
                    `${r.version || ''}-${r.python_bin || ''}-${r.workerId || 'local'}`
                  }
                  loading={ivLoading}
                  columns={interpreterColumns}
                  dataSource={installed}
                  minWidth={900}
                  fixedActions
                  pagination={false}
                  size="middle"
                />
              ),
            },
          ]}
        />
      </Card>

      <Modal
        open={pkgModal.open}
        onCancel={() => setPkgModal({ open: false })}
        title={`依赖列表 - ${pkgModal.venv?.key || pkgModal.venv?.version || ''}`}
        footer={[
          <Button key="close" onClick={() => setPkgModal({ open: false })}>
            关闭
          </Button>,
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
                    title={
                      <Space>
                        <Text strong>{item.name}</Text>
                        <Tag>{item.version}</Tag>
                      </Space>
                    }
                  />
                </List.Item>
              )}
            />
          ) : (
            <Empty description="暂无依赖包" />
          )}
        </Spin>
      </Modal>

      <EditVenvKeyModal
        open={editModal.open}
        venv={editModal.venv}
        onClose={() => setEditModal({ open: false })}
        onSuccess={() => {
          setEditModal({ open: false })
          fetchAllEnvs()
        }}
      />

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
