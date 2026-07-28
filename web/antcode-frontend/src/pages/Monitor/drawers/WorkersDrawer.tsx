import { ClockCircleOutlined, CloudServerOutlined } from '@ant-design/icons'
import { Card, Col, Drawer, Progress, Row, Tag } from 'antd'
import { OsIcon } from '../StatusIcons'
import { getOsName, getStatusColor, getStatusText } from '../status'
import { TaskIndicators } from '../sections/WorkersSection'
import type { WorkerDisplayData } from '../types'

interface WorkersDrawerProps {
  open: boolean
  workers: WorkerDisplayData[]
  onClose: () => void
  onSelect: (worker: WorkerDisplayData) => void
}

const metricColor = (value: number, normal: string) => value > 80 ? '#ff4d4f' : value > 60 ? '#faad14' : normal

const DrawerMetric = ({ label, value, color }: { label: string; value: number; color: string }) => (
  <div className="metric-item-drawer">
    <div className="metric-label-drawer"><span>{label}</span><span>{Math.round(value)}%</span></div>
    <Progress percent={Math.round(value)} strokeColor={metricColor(value, color)} showInfo={false} size="small" />
  </div>
)

const DrawerWorkerCard = ({ worker, onSelect }: { worker: WorkerDisplayData; onSelect: () => void }) => (
  <Card className={`worker-card-drawer worker-${worker.status}`} hoverable onClick={onSelect} size="small">
    <div className="worker-header-drawer">
      <div style={{ flex: 1 }}>
        <h4>{worker.name}</h4>
        <p className="worker-version"><OsIcon os={worker.os} /> {getOsName(worker.os)} · {worker.version}</p>
      </div>
      <Tag color={getStatusColor(worker.status)} style={{ fontSize: 10 }}>{getStatusText(worker.status)}</Tag>
    </div>
    <div className="worker-metrics-drawer">
      <DrawerMetric label="CPU" value={worker.cpu} color="#1890ff" />
      <DrawerMetric label="内存" value={worker.memory} color="#52c41a" />
      <div className="metric-item-drawer">
        <div className="metric-label-drawer"><span>任务</span><span>{worker.tasks}个</span></div>
        <TaskIndicators count={worker.tasks} />
      </div>
      <div className="worker-uptime-drawer"><ClockCircleOutlined style={{ fontSize: 10 }} /> {worker.uptime}</div>
    </div>
  </Card>
)

export const WorkersDrawer = ({ open, workers, onClose, onSelect }: WorkersDrawerProps) => {
  const handleSelect = (worker: WorkerDisplayData) => {
    onClose()
    window.setTimeout(() => onSelect(worker), 100)
  }
  return (
    <Drawer title={<><CloudServerOutlined /> 全部Worker 状态</>} placement="right" width={800} onClose={onClose} open={open}>
      <Row gutter={[12, 12]}>
        {workers.map((worker) => (
          <Col key={worker.id} span={8}>
            <DrawerWorkerCard worker={worker} onSelect={() => handleSelect(worker)} />
          </Col>
        ))}
      </Row>
    </Drawer>
  )
}
