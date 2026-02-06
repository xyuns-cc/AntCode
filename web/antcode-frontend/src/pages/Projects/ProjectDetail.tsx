import type React from 'react'
import { useEffect, useState, useMemo, Suspense, lazy } from 'react'
import { Card, Descriptions, Tag, Button, Space, Skeleton, Typography, Collapse, Modal, Select, Input, Tooltip } from 'antd'
import { EditOutlined, PlayCircleOutlined, ArrowLeftOutlined, FolderOutlined } from '@ant-design/icons'
import { useParams, useNavigate } from 'react-router-dom'
import ResponsiveTable from '@/components/common/ResponsiveTable'
import CopyableTooltip from '@/components/common/CopyableTooltip'
import { projectService } from '@/services/projects'
import { formatDate, formatFileSize } from '@/utils/format'
import { useThemeContext } from '@/contexts/ThemeContext'
import Logger from '@/utils/logger'
import {
  getProjectTypeText,
  getProjectStatusText,
  getProjectTypeColor,
  getProjectStatusColor
} from '@/utils/projectUtils'
import type { Project } from '@/types'
import envService, { type VenvScope } from '@/services/envs'
import { venvScopeOptions, interpreterSourceOptions } from '@/config/displayConfig'

const ProjectEditDrawer = lazy(() => import('@/components/projects/ProjectEditDrawer'))

const { Text, Paragraph } = Typography
const { Panel } = Collapse

const ProjectDetail: React.FC = () => {
  const { id } = useParams<{ id: string }>()
  const navigate = useNavigate()
  const { isDark } = useThemeContext()
  const [project, setProject] = useState<Project | null>(null)
  const [loading, setLoading] = useState(true)
  const [editDrawerOpen, setEditDrawerOpen] = useState(false)
  const [depsModalOpen, setDepsModalOpen] = useState(false)
  const [newDeps, setNewDeps] = useState<string[]>([])
  const [pkgList, setPkgList] = useState<Array<{ name: string; version: string }>>([])
  const [envModalOpen, setEnvModalOpen] = useState(false)
  const [venvScope, setVenvScope] = useState<VenvScope>('private')
  const [pythonVersion, setPythonVersion] = useState<string>('')
  const [versions, setVersions] = useState<string[]>([])
  const [sharedKey, setSharedKey] = useState<string>('')
  const [sharedOptions, setSharedOptions] = useState<{ key: string; version: string }[]>([])
  const [interpreterSource, setInterpreterSource] = useState<string>('mise')
  const [pythonBin, setPythonBin] = useState<string>('')

  const dependencySuggestions = useMemo(() => {
    const popular = [
      'requests',
      'httpx',
      'fastapi',
      'uvicorn',
      'pydantic',
      'sqlalchemy',
      'aiohttp',
      'beautifulsoup4',
      'lxml',
      'numpy',
      'pandas',
      'pyyaml',
      'loguru',
      'pytest',
      'pytest-asyncio',
      'tenacity',
      'redis',
      'celery',
      'apscheduler'
    ]

    const installed = new Set((pkgList || []).map((item: { name: string }) => item.name))

    return popular
      .filter(name => !installed.has(name))
      .map(name => ({ value: name, label: name }))
  }, [pkgList])

  // 动态样式函数
  const getCodeBlockStyle = () => ({
    background: isDark ? '#1f1f1f' : '#f5f5f5',
    color: isDark ? 'rgba(255, 255, 255, 0.88)' : 'rgba(0, 0, 0, 0.88)',
    padding: '8px',
    borderRadius: '4px',
    border: isDark ? '1px solid #434343' : '1px solid #d9d9d9'
  })

  const getCodeContentStyle = () => ({
    background: isDark ? '#1f1f1f' : '#f5f5f5',
    color: isDark ? 'rgba(255, 255, 255, 0.88)' : 'rgba(0, 0, 0, 0.88)',
    padding: '12px',
    borderRadius: '4px',
    maxHeight: '400px',
    overflow: 'auto' as const,
    border: isDark ? '1px solid #434343' : '1px solid #d9d9d9'
  })

  useEffect(() => {
    const fetchProject = async () => {
      if (!id) return

      try {
        setLoading(true)
        const data = await projectService.getProject(id)
        setProject(data)
      } catch (error) {
        Logger.error('Failed to fetch project:', error)
      } finally {
        setLoading(false)
      }
    }

    fetchProject()
  }, [id])

  const openDepsModal = async () => {
    if (!id) return
    const pkgs = await envService.listProjectVenvPackages(id)
    setPkgList(pkgs)
    setDepsModalOpen(true)
  }

  const addDeps = async () => {
    if (!id || newDeps.length === 0) return
    await envService.installPackagesToProjectVenv(id, newDeps)
    const pkgs = await envService.listProjectVenvPackages(id)
    setPkgList(pkgs)
    setNewDeps([])
  }

  const openEnvModal = async () => {
    setEnvModalOpen(true)
    const v = await envService.listPythonVersions()
    setVersions(v)
    const shared = await envService.listVenvs({ scope: 'shared', page: 1, size: 100 })
    setSharedOptions((shared.items || []).map(it => ({ key: it.key || it.version, version: it.version })))
  }

  // 添加编辑成功的处理函数
  const handleEditSuccess = () => {
    // 重新获取项目数据
    if (id) {
      const fetchProject = async () => {
        try {
          const data = await projectService.getProject(id)
          setProject(data)
        } catch (error) {
          Logger.error('Failed to refresh project data:', error)
        }
      }
      fetchProject()
    }
  }

  if (loading) {
    return (
      <div style={{ padding: '24px' }}>
        <Card>
          <Skeleton active paragraph={{ rows: 8 }} />
        </Card>
      </div>
    )
  }

  if (!project) {
    return (
      <div style={{ textAlign: 'center', padding: '50px' }}>
        <div>项目不存在</div>
        <Button
          style={{ marginTop: '16px' }}
          onClick={() => navigate('/projects')}
        >
          返回项目列表
        </Button>
      </div>
    )
  }

  // 渲染文件项目详情
  const renderFileInfo = () => {
    if (!project.file_info) return null

    return (
      <Card title="文件项目详情" style={{ marginTop: 16 }}>
        <Descriptions column={2} bordered>
          <Descriptions.Item label="原始文件名">
            {project.file_info.original_name}
          </Descriptions.Item>
          <Descriptions.Item label="文件大小">
            {formatFileSize(project.file_info.file_size || 0)}
          </Descriptions.Item>
          <Descriptions.Item label="文件类型">
            {project.file_info.file_type || '未知'}
          </Descriptions.Item>
          <Descriptions.Item label="入口文件">
            {project.file_info.entry_point || '未指定'}
          </Descriptions.Item>
          <Descriptions.Item label="文件哈希">
            <Text code>{project.file_info.file_hash}</Text>
          </Descriptions.Item>
          <Descriptions.Item label="存储路径">
            <Text code>
              {project.file_info.file_path || project.file_info.original_file_path || '未提供'}
            </Text>
          </Descriptions.Item>
        </Descriptions>

        {project.file_info.runtime_config && (
          <div style={{ marginTop: 16 }}>
            <Text strong>运行时配置:</Text>
            <Paragraph>
              <pre style={getCodeBlockStyle()}>
                {JSON.stringify(project.file_info.runtime_config, null, 2)}
              </pre>
            </Paragraph>
          </div>
        )}

        {project.file_info.environment_vars && (
          <div style={{ marginTop: 16 }}>
            <Text strong>环境变量:</Text>
            <Paragraph>
              <pre style={getCodeBlockStyle()}>
                {JSON.stringify(project.file_info.environment_vars, null, 2)}
              </pre>
            </Paragraph>
          </div>
        )}
      </Card>
    )
  }

  // 渲染规则项目详情
  const renderRuleInfo = () => {
    if (!project.rule_info) return null

    interface ExtractionRule {
      page_type?: string
      desc?: string
      type?: string
      expr?: string
    }

    const extractionRules: ExtractionRule[] = Array.isArray(project.rule_info.extraction_rules)
      ? project.rule_info.extraction_rules
      : []

    const listRules = extractionRules.filter((rule) => rule.page_type === 'list')
    const detailRules = extractionRules.filter((rule) => rule.page_type === 'detail')

    const columns = [
      {
        title: '规则描述',
        dataIndex: 'desc',
        key: 'desc',
        width: 150,
        ellipsis: { showTitle: false },
        render: (desc: string) => (
          <Tooltip title={desc} placement="topLeft">
            <span>{desc}</span>
          </Tooltip>
        )
      },
      {
        title: '规则类型',
        dataIndex: 'type',
        key: 'type',
        width: 100,
        render: (type: string) => <Tag color="blue">{type.toUpperCase()}</Tag>
      },
      {
        title: '选择器表达式',
        dataIndex: 'expr',
        key: 'expr',
        ellipsis: { showTitle: false },
        render: (expr: string) => (
          <Tooltip title={expr} placement="topLeft">
            <Text code style={{ wordBreak: 'break-all' }}>{expr}</Text>
          </Tooltip>
        )
      }
    ]

    return (
      <Card title="规则项目详情" style={{ marginTop: 16 }}>
        <Descriptions column={2} bordered>
          <Descriptions.Item label="采集引擎">
            <Tag color="green">{project.rule_info.engine}</Tag>
          </Descriptions.Item>
          <Descriptions.Item label="目标URL">
            <CopyableTooltip text={project.rule_info.target_url}>
              <span style={{ cursor: 'pointer' }}>
                {project.rule_info.target_url}
              </span>
            </CopyableTooltip>
          </Descriptions.Item>
          <Descriptions.Item label="回调类型">
            <Tag color="blue">{project.rule_info.callback_type}</Tag>
          </Descriptions.Item>
          <Descriptions.Item label="请求方法">
            {project.rule_info.request_method}
          </Descriptions.Item>
          <Descriptions.Item label="请求间隔">
            {project.rule_info.request_delay}ms
          </Descriptions.Item>
          <Descriptions.Item label="最大页数">
            {project.rule_info.max_pages}
          </Descriptions.Item>
        </Descriptions>

        <Collapse style={{ marginTop: 16 }}>
          {project.rule_info.callback_type === 'mixed' ? (
            <>
              <Panel header={`列表页规则 (${listRules.length})`} key="list">
                <ResponsiveTable
                  columns={columns}
                  dataSource={listRules}
                  rowKey={(record, idx) => `${record.type}-${record.expr}-${idx}`}
                  pagination={false}
                  size="small"
                />
              </Panel>
              <Panel header={`详情页规则 (${detailRules.length})`} key="detail">
                <ResponsiveTable
                  columns={columns}
                  dataSource={detailRules}
                  rowKey={(record, idx) => `${record.type}-${record.expr}-${idx}`}
                  pagination={false}
                  size="small"
                />
              </Panel>
            </>
          ) : (
            <Panel header={`提取规则 (${extractionRules.length})`} key="all">
              <ResponsiveTable
                columns={columns}
                dataSource={extractionRules}
                rowKey={(record, idx) => `${record.type}-${record.expr}-${idx}`}
                pagination={false}
                size="small"
              />
            </Panel>
          )}

          {project.rule_info.headers && (
            <Panel header="请求头配置" key="headers">
              <pre style={getCodeBlockStyle()}>
                {JSON.stringify(project.rule_info.headers, null, 2)}
              </pre>
            </Panel>
          )}

          {project.rule_info.pagination_config && (
            <Panel header="分页配置" key="pagination">
              <pre style={getCodeBlockStyle()}>
                {JSON.stringify(project.rule_info.pagination_config, null, 2)}
              </pre>
            </Panel>
          )}
        </Collapse>
      </Card>
    )
  }

  // 渲染代码项目详情
  const renderCodeInfo = () => {
    if (!project.code_info) return null

    return (
      <Card title="代码项目详情" style={{ marginTop: 16 }}>
        <Descriptions column={2} bordered>
          <Descriptions.Item label="编程语言">
            <Tag color="blue">{project.code_info.language}</Tag>
          </Descriptions.Item>
          <Descriptions.Item label="版本">
            {project.code_info.version}
          </Descriptions.Item>
          <Descriptions.Item label="入口函数">
            {project.code_info.entry_point || '未指定'}
          </Descriptions.Item>
          <Descriptions.Item label="内容大小">
            {(project.code_info.content.length / 1024).toFixed(2)} KB
          </Descriptions.Item>
        </Descriptions>

        <div style={{ marginTop: 16 }}>
          <Text strong>代码内容:</Text>
          <div style={getCodeContentStyle()}>
            <pre style={{ margin: 0, fontFamily: 'Monaco, Consolas, monospace', fontSize: '12px' }}>
              {project.code_info.content}
            </pre>
          </div>
        </div>



        {project.code_info.runtime_config && (
          <div style={{ marginTop: 16 }}>
            <Text strong>运行时配置:</Text>
            <Paragraph>
              <pre style={getCodeBlockStyle()}>
                {JSON.stringify(project.code_info.runtime_config, null, 2)}
              </pre>
            </Paragraph>
          </div>
        )}

        {project.code_info.environment_vars && (
          <div style={{ marginTop: 16 }}>
            <Text strong>环境变量:</Text>
            <Paragraph>
              <pre style={getCodeBlockStyle()}>
                {JSON.stringify(project.code_info.environment_vars, null, 2)}
              </pre>
            </Paragraph>
          </div>
        )}
      </Card>
    )
  }

  return (
    <>
      <div>
        <Card
          title={
            <Space>
              <Button
                type="text"
                icon={<ArrowLeftOutlined />}
                onClick={() => navigate('/projects')}
              >
                返回
              </Button>
              <span>{project.name}</span>
            </Space>
          }
          extra={
            <Space>
              <Button
                type="primary"
                icon={<PlayCircleOutlined />}
                onClick={() => navigate(`/tasks/create?project_id=${project.id}`)}
              >
                创建任务
              </Button>
              {project.type === 'file' && (
                <Button
                  icon={<FolderOutlined />}
                  onClick={() => navigate(`/projects/${project.id}/files`)}
                >
                  文件管理
                </Button>
              )}
              <Button
                icon={<EditOutlined />}
                onClick={() => setEditDrawerOpen(true)}
              >
                编辑
              </Button>
            </Space>
          }
        >
          <Descriptions column={2} bordered>
            <Descriptions.Item label="项目名称">
              {project.name}
            </Descriptions.Item>
            <Descriptions.Item label="项目类型">
              <Tag color={getProjectTypeColor(project.type)}>
                {getProjectTypeText(project.type)}
              </Tag>
            </Descriptions.Item>
            <Descriptions.Item label="项目状态">
              <Tag color={getProjectStatusColor(project.status)}>
                {getProjectStatusText(project.status)}
              </Tag>
            </Descriptions.Item>
            <Descriptions.Item label="创建时间">
              {formatDate(project.created_at)}
            </Descriptions.Item>
            <Descriptions.Item label="更新时间">
              {formatDate(project.updated_at)}
            </Descriptions.Item>
            <Descriptions.Item label="创建者">
              {project.created_by_username || `用户${project.created_by}`}
            </Descriptions.Item>
            <Descriptions.Item label="项目标签" span={2}>
              {Array.isArray(project.tags) && project.tags.length > 0 ? (
                project.tags.map((tag, index) => (
                  <Tag key={index} color="blue">
                    {tag}
                  </Tag>
                ))
              ) : (
                '无'
              )}
            </Descriptions.Item>
            <Descriptions.Item label="项目描述" span={2}>
              {project.description || '无描述'}
            </Descriptions.Item>
          </Descriptions>
        </Card>

        {/* 根据项目类型显示详情信息 */}
        {project.type === 'file' && renderFileInfo()}
        {project.type === 'rule' && renderRuleInfo()}
        {project.type === 'code' && renderCodeInfo()}

        {/* 环境信息 */}
        <Card title="运行环境" style={{ marginTop: 16 }} extra={
          <Space>
            <Button onClick={openDepsModal}>依赖</Button>
            <Button onClick={openEnvModal}>绑定/切换环境</Button>
            <Button danger onClick={async () => { if (!id) return; await envService.createOrBindProjectVenv(id, { version: project.python_version || '', venv_scope: 'private', create_if_missing: true }); }}>重建私有环境</Button>
            <Button danger onClick={async () => { if (!id) return; await envService.deleteProjectVenv(id); }}>删除环境</Button>
          </Space>
        }>
          <Descriptions column={1} bordered>
            <Descriptions.Item label="作用域">{project.runtime_scope || '-'}</Descriptions.Item>
            <Descriptions.Item label="Python版本">{project.python_version || '-'}</Descriptions.Item>
            <Descriptions.Item label="虚拟环境路径">
              {project.venv_path ? (
                <CopyableTooltip text={project.venv_path}>
                  <span style={{ cursor: 'pointer' }}>
                    {project.venv_path}
                  </span>
                </CopyableTooltip>
              ) : '-'}
            </Descriptions.Item>
          </Descriptions>
        </Card>
      </div>

      {/* 编辑抽屉 */}
      <Suspense fallback={null}>
        <ProjectEditDrawer
          open={editDrawerOpen}
          onClose={() => setEditDrawerOpen(false)}
          project={project}
          onSuccess={handleEditSuccess}
        />
      </Suspense>

      {/* 依赖管理 */}
      <Modal open={depsModalOpen} onCancel={() => setDepsModalOpen(false)} title="依赖管理" onOk={addDeps} okText="安装新增依赖">
        <Space direction="vertical" style={{ width: '100%' }}>
          <div>
            <Text strong>已安装依赖：</Text>
          </div>
          <div>
            {(pkgList || []).map((p: { name: string; version: string }) => (<Tag key={p.name}>{p.name}@{p.version}</Tag>))}
          </div>
          <div>
            <Text strong>新增依赖：</Text>
          </div>
          <Select
            mode="tags"
            style={{ width: '100%' }}
            placeholder="输入包名后回车，如: requests==2.32.3"
            value={newDeps}
            onChange={(value: string[]) => setNewDeps(value)}
            tokenSeparators={[',', ' ']}
            options={dependencySuggestions}
            optionFilterProp="value"
            showSearch
            getPopupContainer={(triggerNode) => triggerNode.parentElement || document.body}
            dropdownStyle={{ maxHeight: 260, overflow: 'auto' }}
          />
        </Space>
      </Modal>

      {/* 环境绑定 */}
      <Modal open={envModalOpen} onCancel={() => setEnvModalOpen(false)} title="绑定/切换环境" onOk={async () => {
        if (!id || !pythonVersion) return
        await envService.createOrBindProjectVenv(id, { version: pythonVersion, venv_scope: venvScope, shared_venv_key: venvScope === 'shared' ? sharedKey || undefined : undefined, create_if_missing: true, interpreter_source: interpreterSource, python_bin: interpreterSource === 'local' ? pythonBin : undefined })
        setEnvModalOpen(false)
        handleEditSuccess()
      }}>
        <Space direction="vertical" style={{ width: '100%' }}>
          <Select value={venvScope} onChange={(value) => setVenvScope(value as VenvScope)} options={venvScopeOptions} />
          <Select showSearch placeholder="选择Python版本" value={pythonVersion} onChange={(value: string) => setPythonVersion(value)} options={(versions || []).map(v => ({ value: v, label: v }))} />
          {venvScope === 'shared' && (
            <Select showSearch placeholder="选择共享标识" value={sharedKey} onChange={(value: string) => setSharedKey(value)} options={(sharedOptions || []).map(o => ({ value: o.key, label: `${o.key} (${o.version})` }))} />
          )}
          <Select value={interpreterSource} onChange={(value: string) => setInterpreterSource(value)} options={interpreterSourceOptions} />
          {interpreterSource === 'local' && (
            <Input placeholder="本地 python 路径，如 /usr/local/bin/python3" value={pythonBin} onChange={(e) => setPythonBin(e.target.value)} />
          )}
        </Space>
      </Modal>
    </>
  )
}

export default ProjectDetail
