/**
 * 节点环境管理组件
 * 支持查看依赖、编辑环境、安装依赖、删除环境
 */
import React, { useState, useEffect, useCallback } from 'react'
import {
  Table,
  Button,
  Space,
  Tag,
  Modal,
  Form,
  Input,
  Select,
  List,
  Popconfirm,
  Empty,
  Spin,
  Alert,
  Tooltip
} from 'antd'
import {
  PlusOutlined,
  DeleteOutlined,
  EditOutlined,
  ReloadOutlined,
  FolderOutlined,
  FileTextOutlined,
  CloudDownloadOutlined
} from '@ant-design/icons'
import envService, { type NodeEnvItem } from '@/services/envs'
import showNotification from '@/utils/notification'
import { formatDateTime } from '@/utils/format'

interface NodeEnvManagementProps {
  nodeId: string
  nodeName: string
}

const { Option } = Select
const getErrorMessage = (error: unknown, fallback: string): string =>
  error instanceof Error ? error.message : fallback

const NodeEnvManagement: React.FC<NodeEnvManagementProps> = ({ nodeId, nodeName }) => {
  const [envs, setEnvs] = useState<NodeEnvItem[]>([])
  const [loading, setLoading] = useState(false)
  const [selectedEnv, setSelectedEnv] = useState<NodeEnvItem | null>(null)
  
  // 弹窗状态
  const [createModalVisible, setCreateModalVisible] = useState(false)
  const [editModalVisible, setEditModalVisible] = useState(false)
  const [packagesModalVisible, setPackagesModalVisible] = useState(false)
  const [installModalVisible, setInstallModalVisible] = useState(false)
  
  // 包列表
  const [packages, setPackages] = useState<Array<{ name: string; version: string }>>([])
  const [loadingPackages, setLoadingPackages] = useState(false)
  
  // Python版本列表
  const [pythonVersions, setPythonVersions] = useState<string[]>([])
  
  // 表单
  const [createForm] = Form.useForm()
  const [editForm] = Form.useForm()
  const [installForm] = Form.useForm()

  // 加载环境列表
  const loadEnvs = useCallback(async () => {
    setLoading(true)
    try {
      const data = await envService.listNodeEnvs(nodeId)
      setEnvs(data)
    } catch (error: unknown) {
      showNotification('error', getErrorMessage(error, '加载环境列表失败'))
    } finally {
      setLoading(false)
    }
  }, [nodeId])

  // 加载Python版本
  const loadPythonVersions = useCallback(async () => {
    try {
      const data = await envService.getNodePythonVersions(nodeId)
      setPythonVersions(data.available || [])
    } catch (error: unknown) {
      console.error('获取Python版本失败:', error)
    }
  }, [nodeId])

  useEffect(() => {
    loadEnvs()
    loadPythonVersions()
  }, [loadEnvs, loadPythonVersions])

  // 创建环境
  const handleCreate = async () => {
    try {
      const values = await createForm.validateFields()
      await envService.createNodeEnv(nodeId, {
        name: values.name,
        python_version: values.python_version,
        packages: values.packages ? values.packages.split(',').map((p: string) => p.trim()).filter(Boolean) : []
      })
      showNotification('success', '环境创建成功')
      setCreateModalVisible(false)
      createForm.resetFields()
      loadEnvs()
    } catch (error: unknown) {
      showNotification('error', getErrorMessage(error, '创建环境失败'))
    }
  }

  // 编辑环境
  const handleEdit = async () => {
    if (!selectedEnv) return
    try {
      const values = await editForm.validateFields()
      await envService.updateNodeEnv(nodeId, selectedEnv.name, values)
      showNotification('success', '环境更新成功')
      setEditModalVisible(false)
      loadEnvs()
    } catch (error: unknown) {
      showNotification('error', getErrorMessage(error, '更新环境失败'))
    }
  }

  // 删除环境
  const handleDelete = async (envName: string) => {
    try {
      await envService.deleteNodeEnv(nodeId, envName)
      showNotification('success', '环境删除成功')
      loadEnvs()
    } catch (error: unknown) {
      showNotification('error', getErrorMessage(error, '删除环境失败'))
    }
  }

  // 查看包列表
  const handleViewPackages = async (env: NodeEnvItem) => {
    setSelectedEnv(env)
    setPackagesModalVisible(true)
    setLoadingPackages(true)
    try {
      const pkgs = await envService.listNodeEnvPackages(nodeId, env.name)
      setPackages(pkgs)
    } catch (error: unknown) {
      showNotification('error', getErrorMessage(error, '获取包列表失败'))
      setPackages([])
    } finally {
      setLoadingPackages(false)
    }
  }

  // 安装包
  const handleInstallPackages = async () => {
    if (!selectedEnv) return
    try {
      const values = await installForm.validateFields()
      const packageList = values.packages
        .split(',')
        .map((p: string) => p.trim())
        .filter(Boolean)
      
      await envService.installNodeEnvPackages(nodeId, selectedEnv.name, packageList, values.upgrade)
      showNotification('success', '包安装成功')
      setInstallModalVisible(false)
      installForm.resetFields()
      
      // 刷新包列表
      if (packagesModalVisible) {
        handleViewPackages(selectedEnv)
      }
    } catch (error: unknown) {
      showNotification('error', getErrorMessage(error, '安装包失败'))
    }
  }

  // 卸载包
  const handleUninstallPackage = async (packageName: string) => {
    if (!selectedEnv) return
    try {
      await envService.uninstallNodeEnvPackages(nodeId, selectedEnv.name, [packageName])
      showNotification('success', '包卸载成功')
      // 刷新包列表
      handleViewPackages(selectedEnv)
    } catch (error: unknown) {
      showNotification('error', getErrorMessage(error, '卸载包失败'))
    }
  }

  // 打开编辑弹窗
  const openEditModal = (env: NodeEnvItem) => {
    setSelectedEnv(env)
    editForm.setFieldsValue({
      key: env.name,
      description: ''
    })
    setEditModalVisible(true)
  }

  // 打开安装包弹窗
  const openInstallModal = (env: NodeEnvItem) => {
    setSelectedEnv(env)
    installForm.resetFields()
    setInstallModalVisible(true)
  }

  const columns = [
    {
      title: '环境名称',
      dataIndex: 'name',
      key: 'name',
      render: (name: string) => (
        <Space>
          <FolderOutlined />
          <strong>{name}</strong>
        </Space>
      )
    },
    {
      title: 'Python版本',
      dataIndex: 'python_version',
      key: 'python_version',
      render: (version: string) => <Tag color="blue">{version}</Tag>
    },
    {
      title: '包数量',
      dataIndex: 'packages_count',
      key: 'packages_count',
      render: (count: number) => count || 0
    },
    {
      title: '创建时间',
      dataIndex: 'created_at',
      key: 'created_at',
      render: (time: string) => time ? formatDateTime(time) : '-'
    },
    {
      title: '操作',
      key: 'actions',
      width: 280,
      render: (_value: unknown, record: NodeEnvItem) => (
        <Space>
          <Tooltip title="查看依赖">
            <Button
              type="link"
              size="small"
              icon={<FileTextOutlined />}
              onClick={() => handleViewPackages(record)}
            >
              依赖
            </Button>
          </Tooltip>
          <Tooltip title="编辑环境">
            <Button
              type="link"
              size="small"
              icon={<EditOutlined />}
              onClick={() => openEditModal(record)}
            >
              编辑
            </Button>
          </Tooltip>
          <Tooltip title="安装依赖">
            <Button
              type="link"
              size="small"
              icon={<CloudDownloadOutlined />}
              onClick={() => openInstallModal(record)}
            >
              安装
            </Button>
          </Tooltip>
          <Popconfirm
            title="确定要删除此环境吗？"
            onConfirm={() => handleDelete(record.name)}
            okText="确定"
            cancelText="取消"
          >
            <Button
              type="link"
              size="small"
              danger
              icon={<DeleteOutlined />}
            >
              删除
            </Button>
          </Popconfirm>
        </Space>
      )
    }
  ]

  return (
    <div>
      <Alert
        message={`管理节点 "${nodeName}" 上的虚拟环境`}
        type="info"
        showIcon
        style={{ marginBottom: 16 }}
      />

      <Space style={{ marginBottom: 16 }}>
        <Button
          type="primary"
          icon={<PlusOutlined />}
          onClick={() => setCreateModalVisible(true)}
        >
          创建环境
        </Button>
        <Button icon={<ReloadOutlined />} onClick={loadEnvs}>
          刷新
        </Button>
      </Space>

      <Table
        columns={columns}
        dataSource={envs}
        rowKey="name"
        loading={loading}
        locale={{
          emptyText: <Empty description="暂无环境" />
        }}
        pagination={{
          pageSize: 10,
          showSizeChanger: true,
          showTotal: (total) => `共 ${total} 个环境`
        }}
      />

      {/* 创建环境弹窗 */}
      <Modal
        title="创建虚拟环境"
        open={createModalVisible}
        onOk={handleCreate}
        onCancel={() => {
          setCreateModalVisible(false)
          createForm.resetFields()
        }}
        width={600}
        forceRender
      >
        <Form form={createForm} layout="vertical">
          <Form.Item
            label="环境名称"
            name="name"
            rules={[{ required: true, message: '请输入环境名称' }]}
          >
            <Input placeholder="如: my-env" />
          </Form.Item>
          <Form.Item
            label="Python版本"
            name="python_version"
            help="留空则使用系统默认版本"
          >
            <Select placeholder="选择Python版本" allowClear>
              {pythonVersions.map(v => (
                <Option key={v} value={v}>{v}</Option>
              ))}
            </Select>
          </Form.Item>
          <Form.Item
            label="初始包"
            name="packages"
            help="多个包用逗号分隔，如: requests,pandas"
          >
            <Input.TextArea placeholder="requests, pandas, numpy" rows={3} />
          </Form.Item>
        </Form>
      </Modal>

      {/* 编辑环境弹窗 */}
      <Modal
        title="编辑环境"
        open={editModalVisible}
        onOk={handleEdit}
        onCancel={() => setEditModalVisible(false)}
        width={600}
        forceRender
      >
        <Form form={editForm} layout="vertical">
          <Form.Item
            label="环境标识"
            name="key"
            help="环境的别名或标识"
          >
            <Input placeholder="如: production" />
          </Form.Item>
          <Form.Item
            label="描述"
            name="description"
          >
            <Input.TextArea placeholder="环境描述" rows={3} />
          </Form.Item>
        </Form>
      </Modal>

      {/* 查看包列表弹窗 */}
      <Modal
        title={`环境依赖 - ${selectedEnv?.name}`}
        open={packagesModalVisible}
        onCancel={() => setPackagesModalVisible(false)}
        footer={[
          <Button key="close" onClick={() => setPackagesModalVisible(false)}>
            关闭
          </Button>,
          <Button
            key="install"
            type="primary"
            icon={<CloudDownloadOutlined />}
            onClick={() => {
              setPackagesModalVisible(false)
              openInstallModal(selectedEnv!)
            }}
          >
            安装包
          </Button>
        ]}
        width={700}
      >
        <Spin spinning={loadingPackages}>
          {packages.length > 0 ? (
            <List
              dataSource={packages}
              renderItem={(pkg) => (
                <List.Item
                  actions={[
                    <Popconfirm
                      key="uninstall"
                      title="确定要卸载此包吗？"
                      onConfirm={() => handleUninstallPackage(pkg.name)}
                      okText="确定"
                      cancelText="取消"
                    >
                      <Button type="link" size="small" danger>
                        卸载
                      </Button>
                    </Popconfirm>
                  ]}
                >
                  <List.Item.Meta
                    title={<strong>{pkg.name}</strong>}
                    description={`版本: ${pkg.version}`}
                  />
                </List.Item>
              )}
            />
          ) : (
            <Empty description="暂无已安装的包" />
          )}
        </Spin>
      </Modal>

      {/* 安装包弹窗 */}
      <Modal
        title={`安装包 - ${selectedEnv?.name}`}
        open={installModalVisible}
        onOk={handleInstallPackages}
        onCancel={() => {
          setInstallModalVisible(false)
          installForm.resetFields()
        }}
        width={600}
        forceRender
      >
        <Form form={installForm} layout="vertical">
          <Form.Item
            label="包列表"
            name="packages"
            rules={[{ required: true, message: '请输入要安装的包' }]}
            help="多个包用逗号分隔，可指定版本如: requests>=2.0.0, pandas==1.5.0"
          >
            <Input.TextArea
              placeholder="requests, pandas>=2.0.0, numpy"
              rows={4}
            />
          </Form.Item>
          <Form.Item
            label="升级现有包"
            name="upgrade"
            valuePropName="checked"
            initialValue={false}
          >
            <Select>
              <Option value={false}>否</Option>
              <Option value={true}>是</Option>
            </Select>
          </Form.Item>
        </Form>
      </Modal>
    </div>
  )
}

export default NodeEnvManagement
