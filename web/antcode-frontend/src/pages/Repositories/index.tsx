import type React from 'react'
import { useCallback, useEffect, useMemo, useState } from 'react'
import { App, Button, Form, Space, Tag, Typography } from 'antd'
import type { ColumnsType } from 'antd/es/table'
import {
  BranchesOutlined,
  FileSearchOutlined,
  PlusOutlined,
  ReloadOutlined,
} from '@ant-design/icons'
import PageContainer from '@/components/common/PageContainer'
import ResponsiveTable from '@/components/common/ResponsiveTable'
import { repositoryProjectImportService, repositoryService } from '@/services/repositories'
import type { GitRepository, RepositoryScanResult } from '@/types/repository'
import { formatDate } from '@/utils/format'
import CreateRepositoryDrawer from './components/CreateRepositoryDrawer'
import ScanImportDrawer from './components/ScanImportDrawer'
import { buildImportDefaults, buildImportProjects } from './helpers'

const { Text } = Typography

const Repositories: React.FC = () => {
  const { message } = App.useApp()
  const [repositories, setRepositories] = useState<GitRepository[]>([])
  const [loading, setLoading] = useState(false)
  const [createOpen, setCreateOpen] = useState(false)
  const [scanOpen, setScanOpen] = useState(false)
  const [activeRepository, setActiveRepository] = useState<GitRepository | null>(null)
  const [scanResult, setScanResult] = useState<RepositoryScanResult | null>(null)
  const [selectedSubdirs, setSelectedSubdirs] = useState<string[]>([])
  const [form] = Form.useForm()
  const [importForm] = Form.useForm()

  const loadRepositories = async () => {
    setLoading(true)
    try {
      setRepositories(await repositoryService.list())
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    loadRepositories()
  }, [])

  const openScan = useCallback((repository: GitRepository) => {
    setActiveRepository(repository)
    setScanResult(null)
    setSelectedSubdirs([])
    importForm.resetFields()
    setScanOpen(true)
  }, [importForm])

  const columns = useMemo<ColumnsType<GitRepository>>(() => [
    {
      title: '仓库',
      dataIndex: 'name',
      width: 180,
      render: (name: string, record) => (
        <Space direction="vertical" size={0}>
          <Text strong>{name}</Text>
          <Text code ellipsis style={{ maxWidth: 420 }}>{record.url}</Text>
        </Space>
      ),
    },
    {
      title: '默认引用',
      dataIndex: 'default_ref',
      width: 120,
      render: (ref: string) => <Tag icon={<BranchesOutlined />}>{ref}</Tag>,
    },
    {
      title: '扫描状态',
      dataIndex: 'last_scan_status',
      width: 120,
      render: (status: string | null) => (
        <Tag color={status === 'failed' ? 'red' : status === 'success' ? 'green' : 'default'}>
          {status || '-'}
        </Tag>
      ),
    },
    {
      title: '扫描时间',
      dataIndex: 'last_scanned_at',
      width: 180,
      render: (value?: string | null) => value ? formatDate(value) : '-',
    },
    {
      title: '操作',
      key: 'actions',
      fixed: 'right',
      width: 140,
      render: (_, record) => (
        <Button type="link" size="small" icon={<FileSearchOutlined />} onClick={() => openScan(record)}>
          扫描导入
        </Button>
      ),
    },
  ], [openScan])

  const createRepository = async () => {
    const values = await form.validateFields()
    await repositoryService.create(values)
    setCreateOpen(false)
    form.resetFields()
    await loadRepositories()
  }

  const scanRepository = async () => {
    if (!activeRepository) return
    const result = await repositoryService.scan(activeRepository.id, activeRepository.default_ref)
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
          <Button type="primary" icon={<PlusOutlined />} onClick={() => setCreateOpen(true)}>
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
        minWidth={900}
        showIndex={false}
        pagination={{}}
      />
      <CreateRepositoryDrawer
        open={createOpen}
        form={form}
        onClose={() => setCreateOpen(false)}
        onSubmit={createRepository}
      />
      <ScanImportDrawer
        open={scanOpen}
        repository={activeRepository}
        scanResult={scanResult}
        selectedSubdirs={selectedSubdirs}
        form={importForm}
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
