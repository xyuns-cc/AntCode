import type { FC } from 'react'
import { Button, Dropdown, Space, Tag, Typography } from 'antd'
import {
  ClearOutlined,
  DownloadOutlined,
  FullscreenExitOutlined,
  FullscreenOutlined,
  PlayCircleOutlined,
  ReloadOutlined,
  StopOutlined,
} from '@ant-design/icons'
import type {
  ConnectionStatus,
  ExportFormat,
  HistoricalStatus,
  ViewerTheme,
} from './enhancedLogViewerTypes'
import { connectionPresentation, historyPresentation } from './enhancedLogViewerUtils'

const { Text } = Typography

interface HeaderProps {
  connectionStatus: ConnectionStatus
  enableExport: boolean
  history: { sentLines: number; status: HistoricalStatus; truncated: boolean }
  isFullscreen: boolean
  onClear: () => void
  onConnect: () => void
  onDisconnect: () => void
  onExport: (format: ExportFormat) => void
  onReconnect: () => void
  onToggleFullscreen: () => void
  runId: string
  showControls: boolean
  token: ViewerTheme
}

const StreamIdentity: FC<Pick<HeaderProps, 'connectionStatus' | 'history' | 'runId' | 'token'>> = ({
  connectionStatus, history, runId, token,
}) => {
  const connection = connectionPresentation(connectionStatus)
  const historical = historyPresentation(history.status, history)
  return <Space size="middle">
    <div style={{ width: 4, height: 24, background: token.colorPrimary, borderRadius: 2 }} />
    <div>
      <Text strong style={{ fontSize: 16, display: 'block', lineHeight: 1.2 }}>实时日志</Text>
      <Space size="small" style={{ marginTop: 4 }}>
        <Tag color={connection.color} style={{ margin: 0, border: 'none', fontSize: 12 }}>
          {connection.text}
        </Tag>
        <Tag color={historical.color} style={{ margin: 0, border: 'none', fontSize: 12 }}>
          {historical.text}
        </Tag>
        <Text type="secondary" style={{ fontSize: 12 }}>运行ID: {runId.slice(0, 8)}...</Text>
      </Space>
    </div>
  </Space>
}

const StreamControls: FC<Omit<HeaderProps, 'history' | 'runId' | 'showControls' | 'token'>> = (props) => {
  const items = (['txt', 'json', 'csv'] as ExportFormat[]).map((format) => ({
    key: format,
    label: `导出为 ${format.toUpperCase()}`,
    onClick: () => props.onExport(format),
  }))
  const connectionButton = props.connectionStatus === 'disconnected'
    ? <Button type="primary" icon={<PlayCircleOutlined />} onClick={props.onConnect} size="small">连接</Button>
    : <Button danger icon={<StopOutlined />} onClick={props.onDisconnect} size="small">断开</Button>
  return <Space size="small" wrap>
    {connectionButton}
    <Button icon={<ReloadOutlined />} onClick={props.onReconnect} size="small">重连</Button>
    <Button icon={<ClearOutlined />} onClick={props.onClear} size="small">清除</Button>
    {props.enableExport && <Dropdown menu={{ items }} placement="bottomRight">
      <Button icon={<DownloadOutlined />} size="small">导出</Button>
    </Dropdown>}
    <Button
      aria-label={props.isFullscreen ? '退出全屏' : '全屏'}
      icon={props.isFullscreen ? <FullscreenExitOutlined /> : <FullscreenOutlined />}
      onClick={props.onToggleFullscreen}
      size="small"
    />
  </Space>
}

const EnhancedLogViewerHeader: FC<HeaderProps> = (props) => <div style={{
  display: 'flex',
  justifyContent: 'space-between',
  alignItems: 'center',
  marginBottom: 20,
  flexWrap: 'wrap',
  gap: 12,
}}>
  <StreamIdentity {...props} />
  {props.showControls && <StreamControls {...props} />}
</div>

export default EnhancedLogViewerHeader
