import { CloudServerOutlined, RightOutlined } from '@ant-design/icons'
import { Button, Card, Progress, Tag, theme } from 'antd'
import { OsIcon } from '../StatusIcons'
import { getOsName, getStatusColor, getStatusText } from '../status'
import type { WorkerDisplayData } from '../types'

interface WorkersSectionProps {
  workers: WorkerDisplayData[]
  loading: boolean
  lastChecked: string
  onShowAll: () => void
  onSelect: (worker: WorkerDisplayData) => void
}

const metricColor = (value: number, normal: string) => value > 80 ? '#ff4d4f' : value > 60 ? '#faad14' : normal

const WorkerCard = ({ worker, onSelect }: { worker: WorkerDisplayData; onSelect: () => void }) => (
  <Card className={`worker-card-compact worker-${worker.status}`} hoverable onClick={onSelect}>
    <div className="worker-header-compact">
      <div>
        <h4>{worker.name}</h4>
        <p className="worker-version"><OsIcon os={worker.os} /> {getOsName(worker.os)} · {worker.version}</p>
      </div>
      <Tag color={getStatusColor(worker.status)} style={{ fontSize: 10 }}>{getStatusText(worker.status)}</Tag>
    </div>
    <div className="worker-metrics-compact">
      <MetricRow label="CPU" value={worker.cpu} color="#1890ff" />
      <MetricRow label="内存" value={worker.memory} color="#52c41a" />
      <div className="metric-item-compact">
        <div className="metric-label-compact"><span>任务</span><span>{worker.tasks}个</span></div>
        <TaskIndicators count={worker.tasks} mini />
      </div>
    </div>
  </Card>
)

const MetricRow = ({ label, value, color }: { label: string; value: number; color: string }) => (
  <div className="metric-row">
    <span className="metric-label-compact">{label}</span>
    <div className="metric-value-compact">
      <Progress percent={Math.round(value)} strokeColor={metricColor(value, color)} showInfo={false} size="small" style={{ width: '100%' }} />
      <span className="metric-percent">{Math.round(value)}%</span>
    </div>
  </div>
)

export const TaskIndicators = ({ count, mini = false }: { count: number; mini?: boolean }) => (
  <div className={mini ? 'task-indicators-mini' : 'task-indicators-compact'}>
    {Array.from({ length: Math.min(count, 4) }).map((_, index) => (
      <span
        key={index}
        className={`${mini ? 'indicator-mini' : 'indicator'} indicator-${index % 3 === 0 ? 'success' : index % 3 === 1 ? 'warning' : 'error'}`}
      />
    ))}
    {count > 4 && <span className={mini ? 'more-mini' : 'more-compact'}>+{count - 4}</span>}
  </div>
)

export const WorkersSection = ({ workers, loading, lastChecked, onShowAll, onSelect }: WorkersSectionProps) => {
  const { token } = theme.useToken()
  const extra = (
    <div style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
      <span style={{ fontSize: 11, color: token.colorTextSecondary }}>上次检查: {lastChecked}</span>
      <Button size="small" type="link" onClick={onShowAll} style={{ fontSize: 12 }}>查看全部 <RightOutlined /></Button>
    </div>
  )
  return (
    <Card
      size="small"
      title={<span style={{ fontSize: 14 }}><CloudServerOutlined /> 执行 Worker状态 ({workers.length}个)</span>}
      extra={extra}
      style={{ marginBottom: 12 }}
      loading={loading && workers.length === 0}
    >
      <div className="workers-scroll-container">
        {workers.length === 0 && !loading && (
          <div style={{ textAlign: 'center', padding: '40px', color: token.colorTextTertiary, width: '100%' }}>
            暂无Worker 数据，请先添加Worker
          </div>
        )}
        {workers.map((worker) => <WorkerCard key={worker.id} worker={worker} onSelect={() => onSelect(worker)} />)}
      </div>
    </Card>
  )
}
