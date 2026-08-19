import type React from 'react'
import { useCallback, useEffect, useMemo, useState } from 'react'
import { App, Button, Form, Space } from 'antd'
import { PlusOutlined, ReloadOutlined } from '@ant-design/icons'
import PageContainer from '@/components/common/PageContainer'
import ResponsiveTable from '@/components/common/ResponsiveTable'
import { repositoryProjectImportService, repositoryService } from '@/services/repositories'
import type { GitRepository, RepositoryScanResult } from '@/types/repository'
import RepositoryFormDrawer from './components/RepositoryFormDrawer'
import ScanImportDrawer from './components/ScanImportDrawer'
import { buildRepositoryColumns } from './components/repositoryColumns'
import {
  buildImportDefaults,
  buildImportProjects,
  buildRepositoryCreatePayload,
  buildRepositoryUpdatePayload,
} from './helpers'
import type { RepositoryFormValues } from './helpers'

const TABLE_MIN_WIDTH = 900

const Repositories: React.FC = () => {
  const { message } = App.useApp()
  const [repositories, setRepositories] = useState<GitRepository[]>([])
  const [loading, setLoading] = useState(false)
  const [formOpen, setFormOpen] = useState(false)
  const [editing, setEditing] = useState<GitRepository | null>(null)
  const [scanOpen, setScanOpen] = useState(false)
  const [activeRepository, setActiveRepository] = useState<GitRepository | null>(null)
  const [scanRef, setScanRef] = useState('')
  const [scanResult, setScanResult] = useState<RepositoryScanResult | null>(null)
  const [selectedSubdirs, setSelectedSubdirs] = useState<string[]>([])
  const [form] = Form.useForm()
  const [importForm] = Form.useForm()

  const loadRepositories = useCallback(async () => {
    setLoading(true)
    try {
      setRepositories(await repositoryService.list())
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    loadRepositories()
  }, [loadRepositories])

  const openScan = useCallback((repository: GitRepository) => {
    setActiveRepository(repository)
    // 预填默认引用：多数扫描就是照默认来，改了也只影响这一次请求。
    setScanRef(repository.default_ref)
    setScanResult(null)
    setSelectedSubdirs([])
    importForm.resetFields()
    setScanOpen(true)
  }, [importForm])

  const openEdit = useCallback((repository: GitRepository) => {
    setEditing(repository)
    setFormOpen(true)
  }, [])

  const openCreate = useCallback(() => {
    setEditing(null)
    setFormOpen(true)
  }, [])

  const removeRepository = useCallback(async (repository: GitRepository) => {
    try {
      await repositoryService.remove(repository.id)
      message.success(`已删除仓库 ${repository.name}`)
    } catch {
      // 报错文案由 axios 拦截器统一弹出（后端 409「Git 仓库仍被项目引用」原样透出），
      // 这里不重复提示。但 rejection 必须在此消化：antd ActionButton 收到 onConfirm 的
      // rejection 后是 `return Promise.reject(e)`，不接就变成控制台里的 unhandled rejection。
    }
    // 成功要刷新；失败也要刷新 —— 服务端拒绝就说明它的状态和这份列表已经不一致了。
    await loadRepositories()
  }, [loadRepositories, message])

  const columns = useMemo(
    () => buildRepositoryColumns({ onScan: openScan, onEdit: openEdit, onDelete: removeRepository }),
    [openScan, openEdit, removeRepository],
  )

  const submitRepository = async () => {
    const values = (await form.validateFields()) as RepositoryFormValues
    if (editing) {
      await repositoryService.update(editing.id, buildRepositoryUpdatePayload(values))
      message.success(`已更新仓库 ${values.name.trim()}`)
    } else {
      await repositoryService.create(buildRepositoryCreatePayload(values))
    }
    setFormOpen(false)
    form.resetFields()
    await loadRepositories()
  }

  const scanRepository = async () => {
    if (!activeRepository) return
    const result = await repositoryService.scan(activeRepository.id, scanRef.trim() || undefined)
    setScanResult(result)
    const selected = result.candidates.map(item => item.subdir)
    setSelectedSubdirs(selected)
    importForm.setFieldsValue(buildImportDefaults(result, selected))
  }

  const importProjects = async () => {
    if (!scanResult || !activeRepository) return
    const values = await importForm.validateFields()
    const projects = buildImportProjects(values, selectedSubdirs, scanResult, activeRepository)
    await repositoryProjectImportService.importFromRepository({ projects })
    message.success(`已导入 ${projects.length} 个项目`)
    setScanOpen(false)
  }

  return (
    <PageContainer
      title="代码仓库"
      extra={
        <Space>
          <Button icon={<ReloadOutlined />} onClick={loadRepositories} loading={loading}>刷新</Button>
          <Button type="primary" icon={<PlusOutlined />} onClick={openCreate}>
            新增仓库
          </Button>
        </Space>
      }
    >
      <ResponsiveTable<GitRepository>
        fill
        rowKey="id"
        columns={columns}
        dataSource={repositories}
        loading={loading}
        minWidth={TABLE_MIN_WIDTH}
        showIndex={false}
        pagination={{}}
      />
      <RepositoryFormDrawer
        open={formOpen}
        editing={editing}
        form={form}
        onClose={() => setFormOpen(false)}
        onSubmit={submitRepository}
      />
      <ScanImportDrawer
        open={scanOpen}
        repository={activeRepository}
        scanResult={scanResult}
        selectedSubdirs={selectedSubdirs}
        form={importForm}
        scanRef={scanRef}
        onScanRefChange={setScanRef}
        onClose={() => setScanOpen(false)}
        onScan={scanRepository}
        onImport={importProjects}
        onSelectionChange={(next) => {
          setSelectedSubdirs(next)
          if (scanResult) {
            importForm.setFieldsValue(buildImportDefaults(scanResult, next))
          }
        }}
      />
    </PageContainer>
  )
}

export default Repositories
