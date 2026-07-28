import {
  AppleOutlined,
  CheckCircleOutlined,
  CloseCircleOutlined,
  CloudServerOutlined,
  LinuxOutlined,
  WarningOutlined,
  WindowsOutlined,
} from '@ant-design/icons'
import { theme } from 'antd'
import type { MonitorAlert, WorkerOs } from './types'

export const OsIcon = ({ os }: { os: WorkerOs }) => {
  const { token } = theme.useToken()
  const style = { fontSize: 14, marginRight: 4 }
  if (os === 'windows') return <WindowsOutlined style={{ ...style, color: token.colorInfo }} />
  if (os === 'macos') return <AppleOutlined style={{ ...style, color: token.colorTextSecondary }} />
  if (os === 'linux' || ['ubuntu', 'debian', 'centos', 'redhat', 'alpine', 'fedora'].includes(os)) {
    return <LinuxOutlined style={{ ...style, color: token.colorWarning }} />
  }
  return <CloudServerOutlined style={{ ...style, color: token.colorPrimary }} />
}

export const AlertIcon = ({ type }: { type: MonitorAlert['type'] }) => {
  const { token } = theme.useToken()
  if (type === 'error') return <CloseCircleOutlined style={{ color: token.colorError }} />
  if (type === 'warning') return <WarningOutlined style={{ color: token.colorWarning }} />
  return <CheckCircleOutlined style={{ color: token.colorInfo }} />
}
