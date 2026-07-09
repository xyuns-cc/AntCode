import React, { useCallback, useEffect, useMemo, useState } from 'react'
import { App, Space, Tag, theme } from 'antd'
import { CloudServerOutlined } from '@ant-design/icons'
import PageContainer from '@/components/common/PageContainer'
import ResponsiveTable from '@/components/common/ResponsiveTable'
import { runtimeService } from '@/services/runtimes'
import { useWorkerStore } from '@/stores/workerStore'
import { CreateRuntimeEnvModal, EditRuntimeEnvModal, InstallPackagesModal } from './components'
import EnvToolbar from './components/EnvToolbar'
import PackageListModal from './components/PackageListModal'
import { buildEnvColumns } from './envColumns'
import { toWorkerRuntimeEnvItem } from './envTransforms'
import type {
  EditModalState,
  ExtendedRuntimeEnvItem,
  InstallModalState,
  PackageInfo,
  PackageModalState,
} from './types'

const EnvListPage: React.FC = () => {
  const { token } = theme.useToken()
  const { message, modal } = App.useApp()
  const { currentWorker, workers, refreshWorkers } = useWorkerStore()

  const [loading, setLoading] = useState(false)
  const [items, setItems] = useState<ExtendedRuntimeEnvItem[]>([])
  const [searchQuery, setSearchQuery] = useState('')
  const [scopeFilter, setScopeFilter] = useState<string | undefined>(undefined)
  const [workerFilter, setWorkerFilter] = useState<string | undefined>(undefined)
  const [currentPage, setCurrentPage] = useState(1)
  const [pageSize, setPageSize] = useState(20)
  const [pkgModal, setPkgModal] = useState<PackageModalState>({ open: false })
  const [editModal, setEditModal] = useState<EditModalState>({ open: false })
  const [installModal, setInstallModal] = useState<InstallModalState>({ open: false })
  const [createModalOpen, setCreateModalOpen] = useState(false)

  useEffect(() => {
    if (workers.length === 0) {
      refreshWorkers()
    }
  }, [workers.length, refreshWorkers])

  const targetWorkers = useMemo(
    () => currentWorker ? [currentWorker] : workers.filter((worker) => worker.status === 'online'),
    [currentWorker, workers]
  )

  const fetchAllEnvs = useCallback(async () => {
    setLoading(true)
    try {
      const batches = await Promise.all(targetWorkers.map(async (worker) => {
        const envs = await runtimeService.listEnvs(worker.id)
        return envs.map((env) => toWorkerRuntimeEnvItem(worker, env))
      }))
      setItems(batches.flat())
    } catch (error: unknown) {
      message.error(error instanceof Error ? error.message : '获取 Worker 环境失败')
      throw error
    } finally {
      setLoading(false)
    }
  }, [message, targetWorkers])

  useEffect(() => {
    fetchAllEnvs()
  }, [fetchAllEnvs])

  const pageData = useMemo(() => {
    let filtered = [...items]
    if (scopeFilter) {
      filtered = filtered.filter((item) => item.scope === scopeFilter)
    }
    if (!currentWorker && workerFilter) {
      filtered = filtered.filter((item) => item.workerId === workerFilter)
    }
    if (searchQuery) {
      const lowerQuery = searchQuery.toLowerCase().trim()
      filtered = filtered.filter((item) => (
        item.runtime_locator?.toLowerCase().includes(lowerQuery) ||
        item.key?.toLowerCase().includes(lowerQuery) ||
        item.version?.toLowerCase().includes(lowerQuery) ||
        item.workerName?.toLowerCase().includes(lowerQuery)
      ))
    }
    const startIndex = (currentPage - 1) * pageSize
    return { data: filtered.slice(startIndex, startIndex + pageSize), total: filtered.length }
  }, [items, scopeFilter, currentWorker, workerFilter, searchQuery, currentPage, pageSize])

  const workerOptions = useMemo(
    () => workers.filter((worker) => worker.status === 'online').map((worker) => ({
      value: worker.id,
      label: worker.name,
    })),
    [workers]
  )

  const handlePaginationChange = (page: number, size: number) => {
    setCurrentPage(size === pageSize ? page : 1)
    setPageSize(size)
  }

  const showPackages = async (record: ExtendedRuntimeEnvItem) => {
    try {
      const packages = await runtimeService.listPackages(record.workerId!, record.envName!)
      setPkgModal({ open: true, env: record, packages })
    } catch (error: unknown) {
      message.error(error instanceof Error ? error.message : '获取依赖列表失败')
    }
  }

  const deleteEnv = (record: ExtendedRuntimeEnvItem) => {
    modal.confirm({
      title: '确认删除 Worker 环境？',
      content: `将从 Worker ${record.workerName} 删除环境 ${record.envName}`,
      onOk: async () => {
        await runtimeService.deleteEnv(record.workerId!, record.envName!)
        message.success('环境删除成功')
        fetchAllEnvs()
      },
    })
  }

  const uninstallPackage = async (pkg: PackageInfo) => {
    const { env } = pkgModal
    if (!env?.workerId || !env.envName) return

    try {
      await runtimeService.uninstallPackages(env.workerId, env.envName, [pkg.name])
      message.success(`已卸载 ${pkg.name}`)
      setPkgModal({ ...pkgModal, loading: true })
      const packages = await runtimeService.listPackages(env.workerId, env.envName)
      setPkgModal({ open: true, env, packages, loading: false })
    } catch (error: unknown) {
      message.error(error instanceof Error ? error.message : '卸载包失败')
      setPkgModal({ ...pkgModal, loading: false })
    }
  }

  const envColumns = buildEnvColumns({
    onViewPackages: showPackages,
    onEdit: (record) => setEditModal({ open: true, env: record }),
    onInstall: (record) => setInstallModal({ open: true, envId: record.id }),
    onDelete: deleteEnv,
  })

  return (
    <PageContainer
      title={
        <Space>
          <CloudServerOutlined style={{ color: token.colorPrimary }} />
          <span>运行时管理</span>
          {currentWorker && <Tag color="cyan">{currentWorker.name}</Tag>}
        </Space>
      }
      toolbar={
        <EnvToolbar
          activeTab="envs"
          loading={loading}
          hasCurrentWorker={Boolean(currentWorker)}
          workerFilter={workerFilter}
          scopeFilter={scopeFilter}
          searchQuery={searchQuery}
          workerOptions={workerOptions}
          onRefresh={fetchAllEnvs}
          onWorkerFilterChange={(value) => {
            setWorkerFilter(value)
            setCurrentPage(1)
          }}
          onScopeFilterChange={(value) => {
            setScopeFilter(value)
            setCurrentPage(1)
          }}
          onSearchChange={(value) => {
            setSearchQuery(value)
            setCurrentPage(1)
          }}
          onCreate={() => setCreateModalOpen(true)}
        />
      }
    >
      <ResponsiveTable
        fill
        rowKey="id"
        loading={loading}
        columns={envColumns}
        dataSource={pageData.data}
        minWidth={1050}
        fixedActions
        pagination={{
          current: currentPage,
          pageSize,
          total: pageData.total,
          onChange: handlePaginationChange,
          onShowSizeChange: (_, size) => handlePaginationChange(1, size),
        }}
      />

      <PackageListModal
        state={pkgModal}
        onClose={() => setPkgModal({ open: false })}
        onUninstall={uninstallPackage}
      />
      <EditRuntimeEnvModal
        open={editModal.open}
        env={editModal.env}
        onClose={() => setEditModal({ open: false })}
        onSuccess={() => {
          setEditModal({ open: false })
          fetchAllEnvs()
        }}
      />
      <InstallPackagesModal
        open={installModal.open}
        envId={installModal.envId}
        onClose={() => setInstallModal({ open: false })}
        onSuccess={() => {
          setInstallModal({ open: false })
          fetchAllEnvs()
        }}
      />
      <CreateRuntimeEnvModal
        open={createModalOpen}
        workers={workers}
        defaultWorkerId={currentWorker?.id || workerFilter}
        onClose={() => setCreateModalOpen(false)}
        onSuccess={() => {
          setCreateModalOpen(false)
          fetchAllEnvs()
        }}
      />
    </PageContainer>
  )
}

export default EnvListPage
