import { WarningOutlined } from '@ant-design/icons'
import { Badge, Drawer } from 'antd'
import { AlertList } from '../sections/AlertsPerformanceSection'
import type { MonitorAlert } from '../types'

interface AlertsDrawerProps {
  open: boolean
  alerts: MonitorAlert[]
  onClose: () => void
}

export const AlertsDrawer = ({ open, alerts, onClose }: AlertsDrawerProps) => (
  <Drawer
    title={<><WarningOutlined /> 全部告警 <Badge count={alerts.length} style={{ marginLeft: 8 }} /></>}
    placement="right"
    width={500}
    onClose={onClose}
    open={open}
  >
    <div className="alerts-list"><AlertList alerts={alerts} emptyHeight={200} /></div>
  </Drawer>
)
