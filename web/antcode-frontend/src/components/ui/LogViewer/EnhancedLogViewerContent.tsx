import type { FC } from 'react'
import { Space, Tag, Typography } from 'antd'
import VirtualLogViewer from './VirtualLogViewer'
import styles from './LogViewer.module.css'
import type {
  ConnectionStatus,
  LogContentProps,
  LogMessage,
  ViewerTheme,
} from './enhancedLogViewerTypes'
import { connectionPresentation } from './enhancedLogViewerUtils'

const { Text } = Typography

const VIRTUALIZATION_THRESHOLD = 100

const logColors = (message: LogMessage, token: ViewerTheme) => {
  if (message.type === 'stderr' || message.type === 'error') {
    return { color: token.colorError, background: `${token.colorError}15` }
  }
  if (message.type === 'warning') {
    return { color: token.colorWarning, background: `${token.colorWarning}15` }
  }
  if (message.type === 'info') {
    return { color: token.colorPrimary, background: `${token.colorPrimary}15` }
  }
  if (message.type === 'success') {
    return { color: token.colorSuccess, background: `${token.colorSuccess}15` }
  }
  return { color: token.colorSuccess, background: 'transparent' }
}

const LogLine: FC<{ message: LogMessage; token: ViewerTheme }> = ({ message, token }) => {
  const colors = logColors(message, token)
  const metadataStyle = { color: token.colorTextSecondary, marginRight: '8px' }
  return <div className={styles.logLine} style={{
    padding: '4px 8px',
    margin: '1px 0',
    color: colors.color,
    backgroundColor: colors.background,
    borderLeft: `3px solid ${colors.color}`,
    wordBreak: 'break-all',
    whiteSpace: 'pre-wrap',
  }}>
    <span style={{ color: token.colorTextTertiary, marginRight: '8px' }}>
      [{new Date(message.timestamp).toLocaleTimeString()}]
    </span>
    <span style={metadataStyle}>[{message.type.toUpperCase()}]</span>
    {message.level && <span style={metadataStyle}>[{message.level}]</span>}
    {message.source && <span style={metadataStyle}>[{message.source}]</span>}
    <span>{message.content}</span>
  </div>
}

const emptyMessage = (status: ConnectionStatus, allMessageCount: number): string => {
  if (status === 'disconnected') return '点击连接按钮开始查看日志'
  return allMessageCount === 0 ? '等待日志消息...' : '没有匹配的日志消息'
}

const PlainLogContent: FC<LogContentProps> = (props) => <div
  ref={props.containerRef}
  className={styles.logContainer}
  style={{
    height: props.height,
    backgroundColor: props.token.colorBgContainer,
    color: props.token.colorText,
    padding: '12px',
    borderRadius: 6,
    overflow: 'auto',
    border: `1px solid ${props.token.colorBorder}`,
  }}
>
  {props.messages.length === 0
    ? <div style={{ color: props.token.colorTextSecondary, textAlign: 'center', paddingTop: '50px', fontSize: '14px' }}>
      {emptyMessage(props.connectionStatus, props.allMessageCount)}
    </div>
    : props.messages.map((item) => <LogLine key={item.id} message={item} token={props.token} />)}
</div>

export const LogViewerFooter: FC<Pick<LogContentProps,
  'allMessageCount' | 'connectionStatus' | 'maxLines' | 'messages' | 'searchText' | 'token'
>> = (props) => {
  const status = connectionPresentation(props.connectionStatus)
  const viewTruncated = props.allMessageCount >= props.maxLines
  return <div style={{
    marginTop: 16,
    padding: '12px 16px',
    background: props.token.colorFillQuaternary,
    borderRadius: 6,
    display: 'flex',
    justifyContent: 'space-between',
    alignItems: 'center',
    flexWrap: 'wrap',
    gap: 12,
  }}>
    <Text type="secondary" style={{ fontSize: 12 }}>
      显示 <Text strong>{props.messages.length}</Text> / <Text strong>{props.allMessageCount}</Text> 条日志
      {props.searchText && <span> （搜索: "{props.searchText}"）</span>}
      {viewTruncated && <Tag color="warning" style={{ marginLeft: 8, fontSize: 11 }}>
        视图仅保留最新 {props.maxLines} 行，更早日志已截断
      </Tag>}
    </Text>
    <Space size="small">
      <Text type="secondary" style={{ fontSize: 12 }}>连接状态:</Text>
      <Tag color={status.color} style={{ margin: 0, fontSize: 11 }}>{status.text}</Tag>
    </Space>
  </div>
}

const EnhancedLogViewerContent: FC<LogContentProps> = (props) => (
  props.enableVirtualization && props.messages.length > VIRTUALIZATION_THRESHOLD
    ? <VirtualLogViewer
      messages={props.messages}
      height={props.height}
      searchable={false}
      enableAdvancedFilter={false}
      autoScroll={props.autoScroll}
      onAutoScrollChange={props.onAutoScrollChange}
      onClear={props.onClear}
    />
    : <PlainLogContent {...props} />
)

export default EnhancedLogViewerContent
