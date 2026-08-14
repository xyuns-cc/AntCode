/**
 * Worker 安装 Key 弹窗
 * 两步流程：先收集来源绑定（选填），生成成功后再展示一次性凭据
 */
import React, { useEffect, useState } from 'react'
import { Alert, Button, Descriptions, Input, Modal, Space, Typography } from 'antd'
import { LinkOutlined } from '@ant-design/icons'
import { workerService } from '@/services/workers'
import type { WorkerInstallKey } from '@/services/workerServiceContracts'
import { formatDateTime } from '@/utils/format'
import showNotification from '@/utils/notification'

const { Text, Paragraph } = Typography

const PROMPT_MODAL_WIDTH = 560
const RESULT_MODAL_WIDTH = 720
const RESULT_LABEL_WIDTH = 140
const HINT_FONT_SIZE = 12

interface PromptModalProps {
  osType: string | null
  allowedSource: string
  generating: boolean
  onAllowedSourceChange: (value: string) => void
  onGenerate: () => void
  onCancel: () => void
}

/** 来源绑定收集弹窗：只接受管理员显式输入，不做任何自动填充 */
const InstallKeyPromptModal: React.FC<PromptModalProps> = ({
  osType,
  allowedSource,
  generating,
  onAllowedSourceChange,
  onGenerate,
  onCancel,
}) => (
  <Modal
    title={
      <Space>
        <LinkOutlined />
        生成 {osType?.toUpperCase()} 安装 Key
      </Space>
    }
    open={osType !== null}
    onCancel={onCancel}
    onOk={onGenerate}
    okText="生成"
    cancelText="取消"
    confirmLoading={generating}
    width={PROMPT_MODAL_WIDTH}
  >
    <Space direction="vertical" size="middle" style={{ width: '100%' }}>
      <Alert
        type="warning"
        showIcon
        message="安装 Key 是一次性凭据"
        description="生成后请立即复制到目标机器执行，弹窗关闭即从页面清除。"
      />
      <div>
        <Text type="secondary">来源绑定（选填）</Text>
        <Input
          placeholder="目标 Worker 的出口 IP 或网段，如 10.0.0.5 或 10.0.0.0/24"
          value={allowedSource}
          onChange={(e) => onAllowedSourceChange(e.target.value)}
          onPressEnter={onGenerate}
          allowClear
        />
        <Text type="secondary" style={{ fontSize: HINT_FONT_SIZE }}>
          仅接受 IP 或 CIDR。留空表示不预先绑定——Key 仍会在首次使用时锁定到该来源，其他来源无法复用。
        </Text>
      </div>
    </Space>
  </Modal>
)

/** 凭据正文：仅在弹窗内展示，由用户显式点击复制 */
const InstallKeyResultContent: React.FC<{ data: WorkerInstallKey }> = ({ data }) => (
  <Space direction="vertical" size="middle" style={{ width: '100%' }}>
    <Alert
      type="info"
      showIcon
      message="Gateway 首次连接需要安装 Key"
      description="Direct 模式无需安装 Key，会自动生成并持久化 worker_id。"
    />
    <Descriptions
      column={1}
      bordered
      size="small"
      labelStyle={{ width: RESULT_LABEL_WIDTH, whiteSpace: 'nowrap' }}
    >
      <Descriptions.Item label="操作系统">{data.os_type?.toUpperCase?.() || '-'}</Descriptions.Item>
      <Descriptions.Item label="来源绑定">{data.allowed_source || '-'}</Descriptions.Item>
      <Descriptions.Item label="有效期">
        {data.expires_at ? formatDateTime(data.expires_at) : '-'}
      </Descriptions.Item>
    </Descriptions>
    <div>
      <Text type="secondary">安装 Key</Text>
      <Paragraph copyable={{ text: data.key }} style={{ marginBottom: 0 }}>
        <Text code style={{ wordBreak: 'break-all' }}>{data.key}</Text>
      </Paragraph>
    </div>
    <div>
      <Text type="secondary">安装命令</Text>
      <Paragraph copyable={{ text: data.install_command }} style={{ marginBottom: 0 }}>
        <Text code style={{ whiteSpace: 'pre-wrap', wordBreak: 'break-all' }}>
          {data.install_command}
        </Text>
      </Paragraph>
    </div>
  </Space>
)

const InstallKeyResultFooter: React.FC<{ command?: string; onClose: () => void }> = ({
  command,
  onClose,
}) => {
  const handleCopy = async () => {
    if (!command) return
    try {
      await navigator.clipboard.writeText(command)
      showNotification('success', '安装命令已复制到剪贴板')
    } catch (error: unknown) {
      const err = error as { message?: string }
      showNotification('error', err.message || '复制安装命令失败')
    }
  }

  return (
    <Space>
      <Button onClick={handleCopy} disabled={!command}>
        复制安装命令
      </Button>
      <Button type="primary" onClick={onClose}>
        关闭
      </Button>
    </Space>
  )
}

interface WorkerInstallKeyModalsProps {
  /** 待生成安装 Key 的操作系统，null 表示未发起生成 */
  osType: string | null
  /** 结束来源收集（生成成功或用户取消） */
  onClose: () => void
}

const WorkerInstallKeyModals: React.FC<WorkerInstallKeyModalsProps> = ({ osType, onClose }) => {
  const [allowedSource, setAllowedSource] = useState('')
  const [generating, setGenerating] = useState(false)
  const [installKeyData, setInstallKeyData] = useState<WorkerInstallKey | null>(null)
  const [resultVisible, setResultVisible] = useState(false)

  // 每次发起生成都重置来源输入，避免沿用上一次填写的内容
  useEffect(() => {
    if (osType !== null) setAllowedSource('')
  }, [osType])

  // 来源绑定由管理员显式填写（选填，必须是 IP 或 CIDR）：它是一项安全控制，
  // 不能从用户可写的 localStorage 里读，也不能在缺失时静默放行。
  // 留空时后端仍有「首次使用即绑定来源」兜底，Key 不会被第二个来源复用。
  // 凭据只在弹窗内展示，由用户显式点击复制，不自动写入系统剪贴板。
  const handleGenerate = async () => {
    if (!osType) return
    setGenerating(true)
    try {
      const result = await workerService.generateInstallKey(osType, allowedSource.trim() || undefined)
      setInstallKeyData(result)
      onClose()
      setResultVisible(true)
      showNotification('success', `${osType.toUpperCase()} 安装 Key 已生成`)
    } catch (error: unknown) {
      const err = error as { message?: string }
      showNotification('error', err.message || '生成安装 Key 失败')
    } finally {
      setGenerating(false)
    }
  }

  // 关闭弹窗时立即清除凭据，避免安装 Key 在组件树中滞留到页面卸载
  const handleCloseResult = () => {
    setResultVisible(false)
    setInstallKeyData(null)
  }

  return (
    <>
      <InstallKeyPromptModal
        osType={osType}
        allowedSource={allowedSource}
        generating={generating}
        onAllowedSourceChange={setAllowedSource}
        onGenerate={handleGenerate}
        onCancel={onClose}
      />
      <Modal
        title={
          <Space>
            <LinkOutlined />
            Worker 安装 Key
          </Space>
        }
        open={resultVisible}
        onCancel={handleCloseResult}
        footer={
          <InstallKeyResultFooter
            command={installKeyData?.install_command}
            onClose={handleCloseResult}
          />
        }
        width={RESULT_MODAL_WIDTH}
      >
        {installKeyData && <InstallKeyResultContent data={installKeyData} />}
      </Modal>
    </>
  )
}

export default WorkerInstallKeyModals
