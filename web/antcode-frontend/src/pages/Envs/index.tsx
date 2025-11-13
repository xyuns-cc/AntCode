import React, { useEffect, useState, useMemo } from 'react'
import { Card, Table, Space, Tag, Input, Select, Button, Typography, Modal, List, Row, Col, Tabs, Form, Drawer, Steps, Tooltip, Badge, Divider, Upload } from 'antd'
import envService, { type VenvListItem, type VenvScope } from '@/services/envs'
import ResponsiveTable from '@/components/common/ResponsiveTable'

const { Search } = Input
const { Text } = Typography

const EnvListPage: React.FC = () => {
  const [loading, setLoading] = useState(false)
  
  // 所有环境数据（从后端智能加载）
  const [allItems, setAllItems] = useState<VenvListItem[]>([])
  
  // 前端筛选条件
  const [searchQuery, setSearchQuery] = useState('')
  const [scopeFilter, setScopeFilter] = useState<VenvScope | undefined>(undefined)
  const [interpreterSourceFilter, setInterpreterSourceFilter] = useState<'mise' | 'local' | undefined>(undefined)
  
  // 前端分页
  const [currentPage, setCurrentPage] = useState(1)
  const [pageSize, setPageSize] = useState(20)
  
  const [pkgModal, setPkgModal] = useState<{ open: boolean; venv?: VenvListItem; packages?: any[] }>({ open: false })
  const [selectedRowKeys, setSelectedRowKeys] = useState<React.Key[]>([])
  const [editModal, setEditModal] = useState<{ open: boolean; venv?: VenvListItem }>({ open: false })
  const [installModal, setInstallModal] = useState<{ open: boolean; venvId?: number }>({ open: false })

  // 智能加载所有环境数据
  const fetchAllEnvs = async () => {
    setLoading(true)
    try {
      // 先获取第一页，查看总数
      const firstPageResponse = await envService.listVenvs({ page: 1, size: 100 })
      const totalCount = firstPageResponse.total

      // 如果总数小于等于100，直接使用
      if (totalCount <= 100) {
        setAllItems(firstPageResponse.items)
      } else {
        // 如果总数大于100，分批加载（最多加载前10页，即1000条数据）
        const allEnvs = [...firstPageResponse.items]
        const totalPages = Math.ceil(totalCount / 100)
        const pagesToLoad = Math.min(totalPages, 10)
        
        const promises = []
        for (let page = 2; page <= pagesToLoad; page++) {
          promises.push(envService.listVenvs({ page, size: 100 }))
        }
        
        const results = await Promise.all(promises)
        results.forEach(response => {
          allEnvs.push(...response.items)
        })
        
        setAllItems(allEnvs)
      }
    } catch (error) {
      console.error('加载环境列表失败:', error)
      setAllItems([])
    } finally {
      setLoading(false)
    }
  }

  // 前端筛选和分页逻辑
  const filteredAndPaginatedItems = useMemo(() => {
    let filtered = [...allItems]

    // 应用作用域筛选
    if (scopeFilter) {
      filtered = filtered.filter(item => item.scope === scopeFilter)
    }

    // 应用解释器来源筛选
    if (interpreterSourceFilter) {
      filtered = filtered.filter(item => item.interpreter_source === interpreterSourceFilter)
    }

    // 应用搜索（路径、标识、版本）
    if (searchQuery) {
      const lowerQuery = searchQuery.toLowerCase().trim()
      filtered = filtered.filter(item => {
        return (
          item.venv_path?.toLowerCase().includes(lowerQuery) ||
          item.key?.toLowerCase().includes(lowerQuery) ||
          item.version?.toLowerCase().includes(lowerQuery)
        )
      })
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
  }, [allItems, scopeFilter, interpreterSourceFilter, searchQuery, currentPage, pageSize])

  // 初始加载
  useEffect(() => {
    fetchAllEnvs()
  }, [])

  // 处理筛选变化时重置到第一页
  const handleSearchChange = (value: string) => {
    setSearchQuery(value)
    setCurrentPage(1)
  }

  // 实时搜索（输入时立即筛选）
  const handleSearchInput = (e: React.ChangeEvent<HTMLInputElement>) => {
    const value = e.target.value
    setSearchQuery(value)
    setCurrentPage(1)
  }

  const handleScopeChange = (value: VenvScope | undefined) => {
    setScopeFilter(value)
    setCurrentPage(1)
  }

  const handleInterpreterSourceChange = (value: 'mise' | 'local' | undefined) => {
    setInterpreterSourceFilter(value)
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

  const columns = [
    { 
      title: '作用域',
      dataIndex: 'scope',
      key: 'scope',
      width: 80,
      render: (v: VenvScope) => (
        <Tag color={v === 'shared' ? 'green' : 'blue'}>
          {v === 'shared' ? '公共' : '私有'}
        </Tag>
      )
    },
    { 
      title: '标识',
      dataIndex: 'key',
      key: 'key',
      width: 120,
      ellipsis: true,
      render: (v?: string) => v || '-'
    },
    { 
      title: 'Python版本',
      dataIndex: 'version',
      key: 'version',
      width: 100
    },
    { 
      title: '解释器',
      dataIndex: 'interpreter_version',
      key: 'interpreter_version',
      width: 120,
      ellipsis: true,
      render: (v: string) => <Text code>{v}</Text>
    },
    { 
      title: '路径',
      dataIndex: 'venv_path',
      key: 'venv_path',
      ellipsis: true,
      render: (v: string) => <Text copyable>{v}</Text>
    },
    { 
      title: '创建人',
      dataIndex: 'created_by_username',
      key: 'created_by_username',
      width: 100,
      render: (_: any, r: VenvListItem) => r.created_by_username || (r.created_by ?? '-')
    },
    { 
      title: '创建时间',
      dataIndex: 'created_at',
      key: 'created_at',
      width: 180,
      render: (v?: string) => {
        if (!v) return '-'
        // 格式化为 YYYY-MM-DD HH:mm:ss
        const date = v.split('T')[0]
        const time = v.split('T')[1]?.split('.')[0] || ''
        return `${date} ${time}`
      }
    }
  ]

  const [activeTab, setActiveTab] = useState<string>('venvs')
  const [versions, setVersions] = useState<string[]>([])
  const [installed, setInstalled] = useState<Array<{ version: string; python_bin: string; install_dir: string }>>([])
  const [ivLoading, setIvLoading] = useState(false)

  const refreshInterpreters = async () => {
    setIvLoading(true)
    try {
      const [v, ins] = await Promise.all([
        envService.listPythonVersions(),
        envService.listInterpreters(),
      ])
      setVersions(v)
      setInstalled(ins)
    } finally {
      setIvLoading(false)
    }
  }

  useEffect(() => {
    if (activeTab === 'interpreters') {
      refreshInterpreters()
    }
  }, [activeTab])

  return (
    <Card 
      title="环境管理" 
      extra={
        <Space wrap>
          <Select
            allowClear
            placeholder="作用域"
            style={{ width: 120 }}
            value={scopeFilter}
            onChange={handleScopeChange}
            options={[{ value: 'private', label: '私有' }, { value: 'shared', label: '公共' }]}
          />
          <Select
            allowClear
            placeholder="解释器来源"
            style={{ width: 120 }}
            value={interpreterSourceFilter}
            onChange={handleInterpreterSourceChange}
            options={[{ value: 'mise', label: 'mise' }, { value: 'local', label: 'local' }]}
          />
          <Search 
            placeholder="搜索路径/标识/版本" 
            value={searchQuery}
            onChange={handleSearchInput}
            onSearch={handleSearchChange}
            enterButton
            allowClear 
            style={{ width: 220 }}
          />
        </Space>
      }
    >
      <Tabs
        activeKey={activeTab}
        onChange={setActiveTab}
        items={[
          {
            key: 'venvs',
            label: '虚拟环境',
            children: (
              <>
                {/* 操作按钮栏 */}
                <Card 
                  style={{ marginBottom: 16 }}
                  styles={{ body: { padding: '12px 16px' } }}
                >
                  <Space wrap>
                    <CreateVenvDrawer onCreated={fetchAllEnvs} />
                    <Button 
                      danger 
                      disabled={!selectedRowKeys.length} 
                      icon={<DeleteOutlined />}
                      onClick={async () => {
                        Modal.confirm({
                          title: '确定要删除选中的共享环境吗？',
                          content: '该操作不可恢复，且仅对未被项目绑定的共享环境生效。',
                          onOk: async () => {
                            await envService.batchDeleteVenvs(selectedRowKeys as number[])
                            setSelectedRowKeys([])
                            fetchAllEnvs()
                          }
                        })
                      }}
                    >
                      批量删除 {selectedRowKeys.length > 0 && `(${selectedRowKeys.length})`}
                    </Button>
                    <InstallPackagesButton 
                      venvId={-1 as any} 
                      onInstalled={() => {}} 
                      batch 
                      selectedIds={selectedRowKeys as number[]} 
                    />
                    <Button 
                      icon={<ReloadOutlined />}
                      onClick={() => fetchAllEnvs()}
                      loading={loading}
                    >
                      刷新
                    </Button>
                  </Space>
                </Card>

                <ResponsiveTable
                  rowKey="id"
                  loading={loading}
                  rowSelection={{ selectedRowKeys, onChange: setSelectedRowKeys }}
                  columns={[
                    ...columns,
                    {
                      title: '操作',
                      key: 'actions',
                      width: 150,
                      render: (_: any, record: VenvListItem) => (
                        <Space size="small">
                          <Tooltip title="查看依赖">
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
                          <Tooltip title={record.scope === 'shared' ? '编辑标识' : '私有环境不支持编辑'}>
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
                          <Tooltip title="安装依赖">
                            <Button
                              type="link"
                              size="small"
                              icon={<DownloadOutlined />}
                              onClick={() => setInstallModal({ open: true, venvId: record.id })}
                            />
                          </Tooltip>
                          <Tooltip title="删除">
                            <Button
                              type="link"
                              size="small"
                              danger
                              icon={<DeleteOutlined />}
                              onClick={async () => {
                                if (record.scope === 'shared') {
                                  Modal.confirm({ 
                                    title: '确认删除共享环境？',
                                    content: '仅未被项目使用的共享环境可删除',
                                    onOk: async () => { 
                                      await envService.deleteVenv(record.id)
                                      fetchAllEnvs() // 重新加载
                                    }
                                  })
                                } else {
                                  Modal.confirm({ 
                                    title: '确认删除私有环境？',
                                    content: record.current_project_id 
                                      ? `该操作会解除项目(${record.current_project_id})的环境绑定` 
                                      : '该操作将删除该私有环境',
                                    onOk: async () => { 
                                      await envService.deleteVenv(record.id, true)
                                      fetchAllEnvs() // 重新加载
                                    }
                                  })
                                }
                              }}
                            />
                          </Tooltip>
                        </Space>
                      )
                    }
                  ]}
                  dataSource={filteredAndPaginatedItems.data}
                  minWidth={1200}
                  fixedActions={true}
                  pagination={{ 
                    current: currentPage,
                    pageSize: pageSize,
                    total: filteredAndPaginatedItems.total,
                    onChange: (page, size) => handlePaginationChange(page, size || pageSize),
                    onShowSizeChange: (current, size) => handlePaginationChange(1, size),
                    showSizeChanger: true,
                    showQuickJumper: true,
                    showTotal: (total, range) => `第 ${range[0]}-${range[1]} 条，共 ${total} 条记录`,
                    pageSizeOptions: ['10', '20', '50', '100']
                  }}
                  size="middle"
                />
              </>
            )
          },
          {
            key: 'interpreters',
            label: '解释器',
            children: (
              <Card bordered={false}>
                <Space direction="vertical" style={{ width: '100%' }}>
                  <Space>
                    <InterpreterDrawer onAdded={refreshInterpreters} />
                    <Button icon={<ReloadOutlined />} onClick={refreshInterpreters}>刷新</Button>
                  </Space>
                  <ResponsiveTable
                    rowKey={(r) => r.version}
                    loading={ivLoading}
                    columns={[
                      { 
                        title: '版本',
                        dataIndex: 'version',
                        width: 100
                      },
                      { 
                        title: '来源',
                        dataIndex: 'source',
                        width: 80,
                        render: (v: string) => (
                          <Tag color={v === 'mise' ? 'blue' : 'orange'}>
                            {v}
                          </Tag>
                        )
                      },
                      { 
                        title: '路径',
                        dataIndex: 'python_bin',
                        ellipsis: true,
                        render: (v: string) => <Text copyable>{v}</Text>
                      },
                      { 
                        title: '安装目录',
                        dataIndex: 'install_dir',
                        ellipsis: true,
                        render: (v: string) => <Text copyable>{v}</Text>
                      },
                      { 
                        title: '操作',
                        key: 'actions',
                        width: 100,
                        render: (_: any, r: any) => (
                          <Tooltip title={r.source === 'local' ? '删除记录' : '卸载解释器'}>
                            <Button
                              type="link"
                              danger
                              size="small"
                              onClick={async () => {
                                Modal.confirm({
                                  title: r.source === 'local' ? '确认删除记录？' : '确认卸载解释器？',
                                  content: r.source === 'local' 
                                    ? '将从记录中移除该本地解释器'
                                    : `将卸载 mise 管理的 Python ${r.version}`,
                                  onOk: async () => {
                                    await envService.uninstallInterpreter(r.version, r.source)
                                    refreshInterpreters()
                                  }
                                })
                              }}
                            >
                              {r.source === 'local' ? '删除' : '卸载'}
                            </Button>
                          </Tooltip>
                        )
                      }
                    ]}
                    dataSource={installed}
                    minWidth={800}
                    fixedActions={true}
                    pagination={false}
                    size="middle"
                  />
                </Space>
              </Card>
            )
          }
        ]}
      />

      <Modal
        open={pkgModal.open}
        onCancel={() => setPkgModal({ open: false })}
        title={`依赖列表 - ${pkgModal.venv?.key || pkgModal.venv?.version || ''}`}
        footer={<Button onClick={() => setPkgModal({ open: false })}>关闭</Button>}
        width={680}
      >
        <List
          dataSource={pkgModal.packages || []}
          renderItem={(item: any) => (
            <List.Item>
              <Space>
                <Text>{item.name}</Text>
                <Tag>{item.version}</Tag>
              </Space>
            </List.Item>
          )}
        />
      </Modal>

      {/* 编辑标识Modal */}
      <EditVenvKeyModal
        open={editModal.open}
        venv={editModal.venv}
        onClose={() => setEditModal({ open: false })}
        onSuccess={() => {
          setEditModal({ open: false })
          fetchAllEnvs() // 重新加载
        }}
      />

      {/* 安装依赖Modal */}
      <InstallPackagesModal
        open={installModal.open}
        venvId={installModal.venvId}
        onClose={() => setInstallModal({ open: false })}
        onSuccess={() => {
          setInstallModal({ open: false })
          fetchAllEnvs() // 重新加载
        }}
      />
    </Card>
  )
}

export default EnvListPage

import stylesDrawer from '@/components/projects/ProjectCreateDrawer.module.css'
import { 
  InboxOutlined, 
  SettingOutlined, 
  ArrowLeftOutlined, 
  ArrowRightOutlined, 
  CodeOutlined,
  EyeOutlined,
  EditOutlined,
  DeleteOutlined,
  DownloadOutlined,
  ReloadOutlined,
  PlusOutlined
} from '@ant-design/icons'
const { Dragger } = Upload

const CreateVenvDrawer: React.FC<{ onCreated: () => void }> = ({ onCreated }) => {
  const [open, setOpen] = useState(false)
  const [installedInterpreters, setInstalledInterpreters] = useState<Array<{ version: string; source?: string; python_bin: string }>>([])
  const [version, setVersion] = useState<string>('')
  const [interpreterSource, setInterpreterSource] = useState<'mise' | 'local'>('mise')
  const [pythonBin, setPythonBin] = useState<string>('')
  const [sharedKey, setSharedKey] = useState<string>('')
  const [deps, setDeps] = useState<string[]>([])
  const [uploading, setUploading] = useState(false)
  const [currentStep, setCurrentStep] = useState(0)

  const openDrawer = async () => {
    setOpen(true)
    setCurrentStep(0)
    try {
      const list = await envService.listInterpreters()
      setInstalledInterpreters(list as any)
    } catch {
      setInstalledInterpreters([])
    }
  }

  const onUpload = async (file: File) => {
    setUploading(true)
    try {
      const text = await file.text()
      const lines = text.split(/\r?\n/).map(l => l.trim()).filter(l => l && !l.startsWith('#'))
      setDeps(Array.from(new Set([...(deps || []), ...lines])))
    } finally {
      setUploading(false)
    }
    return false
  }

  const submit = async () => {
    if (!version) return
    const info: any = await envService.createSharedVenv({ version, shared_venv_key: sharedKey || undefined, interpreter_source: interpreterSource, python_bin: interpreterSource==='local' ? pythonBin : undefined })
    if (deps && deps.length && info && info.venv_id) {
      await envService.installPackagesToVenv(info.venv_id, deps)
    }
    setOpen(false)
    setVersion('')
    setInterpreterSource('mise')
    setPythonBin('')
    setDeps([])
    setSharedKey('')
    onCreated()
  }

  const steps = [
    { 
      title: '基础配置',
      description: '选择Python版本',
      icon: <SettingOutlined />
    },
    { 
      title: '依赖管理',
      description: '添加项目依赖',
      icon: <InboxOutlined />
    }
  ]

  const renderStepContent = () => {
    switch (currentStep) {
      case 0:
        return (
          <Space direction="vertical" style={{ width: '100%' }} size="large">
            <div>
              <Typography.Title level={5}>Python版本（来源）</Typography.Title>
              <Select
                showSearch
                placeholder="选择已安装的解释器（本地或 mise）"
                value={version}
                onChange={(val, option: any) => {
                  setVersion(val as string)
                  setInterpreterSource((option?.source as 'mise' | 'local') || 'mise')
                  setPythonBin(option?.python_bin as string)
                }}
                options={(installedInterpreters || []).map((it: any) => ({
                  value: it.version,
                  label: `${it.version} (${it.source || 'mise'})`,
                  source: (it as any).source || 'mise',
                  python_bin: it.python_bin,
                }))}
                allowClear
                style={{ width: '100%' }}
                size="large"
              />
              <Typography.Text type="secondary" style={{ fontSize: 12, marginTop: 4, display: 'block' }}>
                列表来自“解释器”管理，支持本地与 mise；标签中已标注来源
              </Typography.Text>
            </div>
            <div>
              <Typography.Title level={5}>共享标识（可选）</Typography.Title>
              <Input 
                placeholder="输入共享标识，便于项目引用" 
                value={sharedKey} 
                onChange={e => setSharedKey(e.target.value)}
                size="large"
              />
              <Typography.Text type="secondary" style={{ fontSize: 12, marginTop: 4, display: 'block' }}>
                设置标识后，其他项目可以通过此标识复用该虚拟环境
              </Typography.Text>
            </div>
          </Space>
        )
      case 1:
        return (
          <Space direction="vertical" style={{ width: '100%' }} size="large">
            <div>
              <Typography.Title level={5}>项目依赖</Typography.Title>
              <Select
                mode="tags"
                placeholder="输入依赖包名后回车，如: requests==2.32.3"
                value={deps}
                onChange={setDeps as any}
                tokenSeparators={[',']}
                style={{ width: '100%' }}
                size="large"
              />
            </div>
            <div>
              <Typography.Title level={5}>上传依赖文件</Typography.Title>
              <Dragger
                accept=".txt"
                showUploadList={false}
                beforeUpload={(file) => {
                  onUpload(file)
                  return false
                }}
                className={stylesDrawer.uploadArea}
                style={{ padding: '20px 0' }}
              >
                <p className="ant-upload-drag-icon">
                  <InboxOutlined style={{ fontSize: 48, color: '#1890ff' }} />
                </p>
                <p className="ant-upload-text">点击或拖拽 requirements.txt 文件到此区域</p>
                <p className="ant-upload-hint">
                  支持上传 requirements.txt 文件自动解析依赖
                </p>
              </Dragger>
              {deps.length > 0 && (
                <div style={{ marginTop: 16 }}>
                  <Typography.Text type="secondary">已添加 {deps.length} 个依赖包</Typography.Text>
                  <div className={stylesDrawer.tagList} style={{ marginTop: 8 }}>
                    {deps.slice(0, 5).map(dep => (
                      <Tag key={dep} closable onClose={() => setDeps(deps.filter(d => d !== dep))}>
                        {dep}
                      </Tag>
                    ))}
                    {deps.length > 5 && <Tag>+{deps.length - 5} more</Tag>}
                  </div>
                </div>
              )}
            </div>
          </Space>
        )
      default:
        return null
    }
  }

  const handleNext = () => {
    if (currentStep === 0 && !version) {
      Modal.warning({ title: '请选择Python版本' })
      return
    }
    setCurrentStep(prev => prev + 1)
  }

  const handlePrev = () => {
    setCurrentStep(prev => prev - 1)
  }

  const renderFooter = () => (
    <Space>
      {currentStep > 0 && (
        <Button
          icon={<ArrowLeftOutlined />}
          onClick={handlePrev}
          disabled={uploading}
        >
          上一步
        </Button>
      )}
      
      {currentStep === 0 && (
        <Button
          type="primary"
          icon={<ArrowRightOutlined />}
          onClick={handleNext}
          disabled={!version}
        >
          下一步
        </Button>
      )}
      
      {currentStep === 1 && (
        <Button 
          type="primary" 
          loading={uploading} 
          onClick={submit}
        >
          创建虚拟环境
        </Button>
      )}
      
      <Button onClick={() => setOpen(false)} disabled={uploading}>
        取消
      </Button>
    </Space>
  )

  return (
    <>
      <Button 
        type="primary" 
        icon={<PlusOutlined />}
        onClick={openDrawer}
      >
        创建虚拟环境
      </Button>
      <Drawer
        title="创建虚拟环境"
        placement="right"
        width={720}
        open={open}
        onClose={() => setOpen(false)}
        maskClosable={false}
        destroyOnHidden
        footer={renderFooter()}
        styles={{ footer: { textAlign: 'right' } }}
        className={stylesDrawer.drawer}
      >
        <div className={stylesDrawer.steps}>
          <Steps current={currentStep} items={steps as any} size="small" />
        </div>
        
        <Card variant="borderless" className={stylesDrawer.formCard}>
          {renderStepContent()}
        </Card>
      </Drawer>
    </>
  )
}

const InstallPackagesButton: React.FC<{ venvId: number; onInstalled?: () => void; batch?: boolean; selectedIds?: number[]; buttonId?: string }> = ({ venvId, onInstalled, batch = false, selectedIds = [], buttonId }) => {
  const [open, setOpen] = useState(false)
  const [pkgs, setPkgs] = useState<string[]>([])
  const [loading, setLoading] = useState(false)
  const submit = async () => {
    if (!pkgs.length) return
    setLoading(true)
    try {
      if (batch) {
        if (!selectedIds.length) {
          Modal.warning({ title: '请先选择要安装依赖的环境' })
          return
        }
        // 批量安装
        for (const id of selectedIds) {
          await envService.installPackagesToVenv(id, pkgs)
        }
      } else {
        await envService.installPackagesToVenv(venvId, pkgs)
      }
      setOpen(false)
      setPkgs([])
      if (onInstalled) {
        onInstalled()
      }
    } finally {
      setLoading(false)
    }
  }
  return (
    <>
      <Button 
        size={batch ? 'middle' : 'small'} 
        onClick={() => setOpen(true)} 
        id={buttonId}
        disabled={batch && !selectedIds.length}
        icon={batch ? <DownloadOutlined /> : undefined}
      >
        {batch ? `批量安装依赖${selectedIds.length > 0 ? ` (${selectedIds.length})` : ''}` : '安装依赖'}
      </Button>
      <Modal open={open} onCancel={() => setOpen(false)} title={batch ? '批量安装依赖' : '安装依赖'} onOk={submit} confirmLoading={loading}>
        <Select mode="tags" style={{ width: '100%' }} placeholder="输入包名后回车，如: requests==2.32.3" value={pkgs} onChange={setPkgs as any} />
      </Modal>
    </>
  )
}

// 编辑虚拟环境标识Modal
const EditVenvKeyModal: React.FC<{ 
  open: boolean; 
  venv?: VenvListItem; 
  onClose: () => void; 
  onSuccess: () => void 
}> = ({ open, venv, onClose, onSuccess }) => {
  const [form] = Form.useForm()
  const [loading, setLoading] = useState(false)

  useEffect(() => {
    if (open && venv) {
      form.setFieldsValue({ key: venv.key || '' })
    }
  }, [open, venv, form])

  const handleSubmit = async () => {
    try {
      const values = await form.validateFields()
      setLoading(true)
      if (venv) {
        await envService.updateSharedVenv(venv.id, { key: values.key || undefined })
        onSuccess()
        form.resetFields()
      }
    } catch (error) {
      // 验证失败或请求失败
    } finally {
      setLoading(false)
    }
  }

  return (
    <Modal
      title="编辑共享环境标识"
      open={open}
      onCancel={onClose}
      onOk={handleSubmit}
      confirmLoading={loading}
      okText="保存"
      cancelText="取消"
      destroyOnHidden
    >
      <Form
        form={form}
        layout="vertical"
        autoComplete="off"
      >
        <Form.Item
          label="共享标识"
          name="key"
          help="设置标识后，其他项目可以通过此标识引用该虚拟环境"
        >
          <Input 
            placeholder="输入共享标识，留空则清除标识" 
            allowClear
          />
        </Form.Item>
        {venv && (
          <div style={{ marginTop: 16 }}>
            <Text type="secondary">当前环境：</Text>
            <div style={{ marginTop: 8 }}>
              <Text>Python {venv.version}</Text>
              {venv.current_project_id && (
                <Tag color="blue" style={{ marginLeft: 8 }}>
                  项目 ID: {venv.current_project_id}
                </Tag>
              )}
            </div>
          </div>
        )}
      </Form>
    </Modal>
  )
}

// 安装依赖Modal
const InstallPackagesModal: React.FC<{ 
  open: boolean; 
  venvId?: number; 
  onClose: () => void; 
  onSuccess: () => void 
}> = ({ open, venvId, onClose, onSuccess }) => {
  const [pkgs, setPkgs] = useState<string[]>([])
  const [loading, setLoading] = useState(false)
  const [uploadLoading, setUploadLoading] = useState(false)

  const handleSubmit = async () => {
    if (!pkgs.length || !venvId) return
    
    setLoading(true)
    try {
      await envService.installPackagesToVenv(venvId, pkgs)
      onSuccess()
      setPkgs([])
    } catch (error) {
      // 错误处理
    } finally {
      setLoading(false)
    }
  }

  const handleUpload = async (file: File) => {
    setUploadLoading(true)
    try {
      const text = await file.text()
      const lines = text.split(/\r?\n/).map(l => l.trim()).filter(l => l && !l.startsWith('#'))
      setPkgs(Array.from(new Set([...pkgs, ...lines])))
    } finally {
      setUploadLoading(false)
    }
    return false
  }

  return (
    <Modal
      title="安装Python依赖包"
      open={open}
      onCancel={onClose}
      onOk={handleSubmit}
      confirmLoading={loading}
      okText="安装"
      cancelText="取消"
      okButtonProps={{ disabled: !pkgs.length }}
      destroyOnHidden
      width={600}
    >
      <Space direction="vertical" style={{ width: '100%' }} size="large">
        <div>
          <Typography.Title level={5}>输入依赖包</Typography.Title>
          <Select 
            mode="tags" 
            style={{ width: '100%' }} 
            placeholder="输入包名后回车，如: requests==2.32.3" 
            value={pkgs} 
            onChange={setPkgs as any}
            size="large"
            tokenSeparators={[',', ' ']}
          />
          <Text type="secondary" style={{ fontSize: 12, marginTop: 4, display: 'block' }}>
            支持多个依赖包，可以指定版本号，例如：numpy==1.21.0 pandas matplotlib
          </Text>
        </div>
        
        <div>
          <Typography.Title level={5}>或上传requirements.txt</Typography.Title>
          <Upload.Dragger
            accept=".txt"
            showUploadList={false}
            beforeUpload={handleUpload}
            disabled={uploadLoading}
          >
            <p className="ant-upload-drag-icon">
              <InboxOutlined style={{ fontSize: 32 }} />
            </p>
            <p className="ant-upload-text">点击或拖拽 requirements.txt 文件到此处</p>
            <p className="ant-upload-hint">支持标准的 requirements.txt 格式</p>
          </Upload.Dragger>
        </div>

        {pkgs.length > 0 && (
          <div>
            <Text strong>待安装的依赖包（{pkgs.length} 个）：</Text>
            <div style={{ marginTop: 8 }}>
              <Space size={[8, 8]} wrap>
                {pkgs.map(pkg => (
                  <Tag 
                    key={pkg} 
                    closable 
                    onClose={() => setPkgs(pkgs.filter(p => p !== pkg))}
                  >
                    {pkg}
                  </Tag>
                ))}
              </Space>
            </div>
            <Button 
              type="link" 
              size="small" 
              onClick={() => setPkgs([])}
              style={{ marginTop: 8 }}
            >
              清空所有
            </Button>
          </div>
        )}
      </Space>
    </Modal>
  )
}

const InterpreterDrawer: React.FC<{ onAdded: () => void }> = ({ onAdded }) => {
  const [open, setOpen] = useState(false)
  const [currentStep, setCurrentStep] = useState(0)
  const [source, setSource] = useState<'mise' | 'local'>('mise')
  const [versions, setVersions] = useState<string[]>([])
  const [version, setVersion] = useState<string>('')
  const [pythonBin, setPythonBin] = useState<string>('')
  const [loading, setLoading] = useState(false)

  const steps = [
    { 
      title: '选择来源',
      description: 'mise 或 本地解释器',
      icon: <SettingOutlined />
    },
    { 
      title: '配置解释器',
      description: '选择版本或填写路径',
      icon: <CodeOutlined />
    }
  ]

  const openDrawer = async () => {
    setOpen(true)
    setCurrentStep(0)
    try {
      const v = await envService.listPythonVersions()
      setVersions(v)
    } catch {
      setVersions([])
    }
  }

  const submit = async () => {
    setLoading(true)
    try {
      if (source === 'mise') {
        if (!version) return
        await envService.installInterpreter(version)
      } else {
        if (!pythonBin) return
        await envService.registerLocalInterpreter(pythonBin)
      }
      setOpen(false)
      setVersion('')
      setPythonBin('')
      onAdded()
    } finally {
      setLoading(false)
    }
  }

  const renderStepContent = () => {
    switch (currentStep) {
      case 0:
        return (
          <Space direction="vertical" style={{ width: '100%' }} size="large">
            <div>
              <Typography.Title level={5}>解释器来源</Typography.Title>
              <Select 
                value={source} 
                onChange={setSource as any} 
                options={[
                  { value: 'mise', label: 'mise（推荐）' },
                  { value: 'local', label: '本地解释器' }
                ]} 
                style={{ width: '100%' }}
                size="large"
              />
              <Typography.Text type="secondary" style={{ fontSize: 12, marginTop: 8, display: 'block' }}>
                {source === 'mise' 
                  ? 'mise 管理的解释器，支持自动下载和版本管理'
                  : '使用系统或手动安装的本地 Python 解释器'
                }
              </Typography.Text>
            </div>
            {source === 'mise' && (
              <Card type="inner" style={{ backgroundColor: '#f0f8ff' }}>
                <Typography.Text>
                  <strong>mise 优势：</strong>
                </Typography.Text>
                <ul style={{ marginTop: 8, paddingLeft: 20 }}>
                  <li>自动管理多个 Python 版本</li>
                  <li>简化环境切换</li>
                  <li>统一的版本管理体验</li>
                </ul>
              </Card>
            )}
          </Space>
        )
      case 1:
        return (
          <Space direction="vertical" style={{ width: '100%' }} size="large">
            <div>
              <Typography.Title level={5}>
                {source === 'mise' ? 'Python 版本' : 'Python 路径'}
              </Typography.Title>
              {source === 'mise' ? (
                <Select 
                  showSearch 
                  placeholder="选择要安装的版本（未安装mise时为空）" 
                  value={version} 
                  onChange={setVersion as any} 
                  options={(versions || []).map(v => ({ value: v, label: v }))} 
                  allowClear 
                  style={{ width: '100%' }}
                  size="large"
                />
              ) : (
                <Input 
                  placeholder="本地 python 路径，如 /usr/local/bin/python3" 
                  value={pythonBin} 
                  onChange={(e) => setPythonBin(e.target.value)} 
                  style={{ width: '100%' }}
                  size="large"
                />
              )}
              <Typography.Text type="secondary" style={{ fontSize: 12, marginTop: 8, display: 'block' }}>
                {source === 'mise' 
                  ? '选择要通过 mise 安装的 Python 版本'
                  : '输入已安装的 Python 解释器完整路径'
                }
              </Typography.Text>
            </div>
            {source === 'local' && (
              <Card type="inner" style={{ backgroundColor: '#fff7e6' }}>
                <Typography.Text type="warning">
                  <strong>注意事项：</strong>
                </Typography.Text>
                <ul style={{ marginTop: 8, paddingLeft: 20 }}>
                  <li>确保路径指向有效的 Python 可执行文件</li>
                  <li>建议使用绝对路径</li>
                  <li>可以使用 which python3 查找路径</li>
                </ul>
              </Card>
            )}
          </Space>
        )
      default:
        return null
    }
  }

  const renderFooter = () => (
    <Space>
      {currentStep > 0 && (
        <Button onClick={() => setCurrentStep(s => s - 1)} disabled={loading}>上一步</Button>
      )}
      {currentStep === 0 && (
        <Button type="primary" onClick={() => setCurrentStep(1)}>下一步</Button>
      )}
      {currentStep === 1 && (
        <Button type="primary" loading={loading} onClick={submit} disabled={(source==='mise' && !version) || (source==='local' && !pythonBin)}>添加</Button>
      )}
      <Button onClick={() => setOpen(false)} disabled={loading}>取消</Button>
    </Space>
  )

  return (
    <>
      <Button type="primary" onClick={openDrawer}>添加解释器</Button>
      <Drawer
        title="添加解释器"
        placement="right"
        width={720}
        open={open}
        onClose={() => setOpen(false)}
        maskClosable={false}
        destroyOnHidden
        footer={renderFooter()}
        styles={{ footer: { textAlign: 'right' } }}
        className={stylesDrawer.drawer}
      >
        <div className={stylesDrawer.steps}>
          <Steps current={currentStep} items={steps as any} size="small" />
        </div>
        
        <Card variant="borderless" className={stylesDrawer.formCard}>
          {renderStepContent()}
        </Card>
      </Drawer>
    </>
  )
}
