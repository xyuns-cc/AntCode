import { Card, Tag, theme } from 'antd'
import { getLogTypeColor, getLogTypeText } from '../status'
import type { WorkerLog } from '../types'

const LogList = ({ logs }: { logs: WorkerLog[] }) => {
  const { token } = theme.useToken()
  if (logs.length === 0) {
    return <div style={{ textAlign: 'center', padding: '20px', color: token.colorTextTertiary }}>暂无日志记录</div>
  }
  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
      {logs.map((log) => (
        <div key={log.id} className={`worker-log-item worker-log-${log.type}`}>
          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', gap: 8 }}>
            <div style={{ flex: 1 }}>
              <Tag color={getLogTypeColor(log.type)} style={{ marginBottom: 4 }}>{getLogTypeText(log.type)}</Tag>
              <div style={{ fontSize: 13, lineHeight: 1.6 }}>{log.message}</div>
            </div>
            <span style={{ fontSize: 11, color: token.colorTextTertiary, whiteSpace: 'nowrap' }}>{log.time}</span>
          </div>
        </div>
      ))}
    </div>
  )
}

export const WorkerLogsCard = ({ logs }: { logs: WorkerLog[] }) => (
  <Card title="Worker 日志" style={{ marginTop: 16 }} size="small">
    <div style={{ maxHeight: 300, overflowY: 'auto' }}><LogList logs={logs} /></div>
  </Card>
)
