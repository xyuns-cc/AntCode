import { ClockCircleOutlined, CloudServerOutlined } from '@ant-design/icons'
import { Card, Col, Drawer, Progress, Row, Tag } from 'antd'
import { OsIcon } from '../StatusIcons'
import { getOsName, getStatusColor, getStatusText } from '../status'
import { TaskIndicators } from '../sections/WorkersSection'
import type { WorkerDisplayData } from '../types'
import { usageBarColor, usageReading, usageText } from '../usage'

interface WorkersDrawerProps {
  open: boolean
  workers: WorkerDisplayData[]
  onClose: () => void
  onSelect: (worker: WorkerDisplayData) => void
}

// value 为 null 时条形留空且不涂色，读数位写占位符（见 ../usage）。
const DrawerMetric = ({ label, value, color }: { label: string; value: number | null; color: string }) => (
  <div className="metric-item-drawer">
    <div className="metric-label-drawer"><span>{label}</span><span>{usageText(value)}</span></div>
    <Progress percent={value ?? 0} strokeColor={usageBarColor(value, color)} showInfo={false} size="small" />
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
      <DrawerMetric label="CPU" value={usageReading(worker, 'cpu')} color="#1890ff" />
      <DrawerMetric label="内存" value={usageReading(worker, 'memory')} color="#52c41a" />
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
