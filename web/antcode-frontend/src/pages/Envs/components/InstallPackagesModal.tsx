import React, { useState } from 'react'
import { Modal, Select, Space, Typography, Tag, Button, Upload, App } from 'antd'
import { InboxOutlined } from '@ant-design/icons'
import envService from '@/services/envs'
import type { InstallPackagesModalProps } from '../types'

const { Text } = Typography

const InstallPackagesModal: React.FC<InstallPackagesModalProps> = ({
  open,
  venvId,
  onClose,
  onSuccess,
}) => {
  const { message } = App.useApp()
  const [pkgs, setPkgs] = useState<string[]>([])
  const [loading, setLoading] = useState(false)
  const [uploadLoading, setUploadLoading] = useState(false)

  const handleSubmit = async () => {
    if (!pkgs.length || !venvId) return

    setLoading(true)
    try {
      // 判断是节点环境还是本地环境
      if (venvId.includes('-')) {
        // 节点环境ID格式: nodeId-envName
        const firstDashIndex = venvId.indexOf('-')
        const nodeId = venvId.substring(0, firstDashIndex)
        const envName = venvId.substring(firstDashIndex + 1)
        await envService.installNodeEnvPackages(nodeId, envName, pkgs)
        message.success('节点环境依赖安装成功')
      } else {
        // 本地环境
        await envService.installPackagesToVenv(venvId, pkgs)
        message.success('依赖安装成功')
      }
      onSuccess()
      setPkgs([])
    } catch (error: unknown) {
      const errMsg = error instanceof Error ? error.message : '安装依赖失败'
      message.error(errMsg)
    } finally {
      setLoading(false)
    }
  }

  const handleUpload = async (file: File) => {
    setUploadLoading(true)
    try {
      const text = await file.text()
      const lines = text
        .split(/\r?\n/)
        .map((l) => l.trim())
        .filter((l) => l && !l.startsWith('#'))
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
            onChange={(value: string[]) => setPkgs(value)}
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
                {pkgs.map((pkg) => (
                  <Tag key={pkg} closable onClose={() => setPkgs(pkgs.filter((p) => p !== pkg))}>
                    {pkg}
                  </Tag>
                ))}
              </Space>
            </div>
            <Button type="link" size="small" onClick={() => setPkgs([])} style={{ marginTop: 8 }}>
              清空所有
            </Button>
          </div>
        )}
      </Space>
    </Modal>
  )
}

export default InstallPackagesModal
