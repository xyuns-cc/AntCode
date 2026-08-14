import type { Worker } from '@/types'
import type {
  MonitorAlert,
  MonitorStats,
  PerformancePeriod,
  WorkerDisplayData,
  WorkerOs,
} from './types'

const formatUptime = (seconds?: number): string => {
  if (!seconds) return '未知'
  const days = Math.floor(seconds / 86400)
  const hours = Math.floor((seconds % 86400) / 3600)
  if (days > 0) return `${days}天 ${hours}小时`
  const minutes = Math.floor((seconds % 3600) / 60)
  return hours > 0 ? `${hours}小时 ${minutes}分钟` : `${minutes}分钟`
}

const mapOsType = (osType?: string): WorkerOs => {
  if (!osType) return 'linux'
  const os = osType.toLowerCase()
  if (os === 'darwin' || os === 'macos') return 'macos'
  if (os === 'windows') return 'windows'
  if (os.includes('ubuntu')) return 'ubuntu'
  if (os.includes('debian')) return 'debian'
  if (os.includes('centos')) return 'centos'
  if (os.includes('redhat') || os.includes('rhel')) return 'redhat'
  if (os.includes('alpine')) return 'alpine'
  if (os.includes('fedora')) return 'fedora'
  return 'linux'
}

const getDisplayStatus = (worker: Worker): WorkerDisplayData['status'] => {
  if (worker.status === 'maintenance' || worker.status === 'connecting') return 'warning'
  if (worker.status !== 'online') return 'stopped'
  const cpu = worker.metrics?.cpu || 0
  const memory = worker.metrics?.memory || 0
  if (cpu > 90 || memory > 90) return 'error'
  if (cpu > 75 || memory > 75) return 'warning'
  return 'running'
}

export const transformWorker = (worker: Worker): WorkerDisplayData => ({
  id: worker.id,
  name: worker.name,
  version: worker.version || 'v1.0.0',
  os: mapOsType(worker.osType),
  status: getDisplayStatus(worker),
  cpu: worker.metrics?.cpu || 0,
  memory: worker.metrics?.memory || 0,
  disk: worker.metrics?.disk || 0,
  tasks: worker.metrics?.runningTasks || 0,
  uptime: formatUptime(worker.metrics?.uptime),
  host: worker.host,
  port: worker.port,
  lastHeartbeat: worker.lastHeartbeat,
  cpuCores: worker.metrics?.cpuCores,
  memoryTotal: worker.metrics?.memoryTotal,
  memoryUsed: worker.metrics?.memoryUsed,
  memoryAvailable: worker.metrics?.memoryAvailable,
  diskTotal: worker.metrics?.diskTotal,
  diskUsed: worker.metrics?.diskUsed,
  diskFree: worker.metrics?.diskFree,
})

export const createAlerts = (workers: WorkerDisplayData[], time: string): MonitorAlert[] => {
  const alerts: MonitorAlert[] = []
  workers.forEach((worker) => {
    addUsageAlert(alerts, worker, 'cpu', 'CPU', time)
    addUsageAlert(alerts, worker, 'memory', '内存', time)
    if (worker.disk > 80) {
      alerts.push({
        id: `disk-${worker.id}`,
        type: 'warning',
        title: '磁盘空间不足',
        message: `${worker.name} Worker磁盘使用率${worker.disk}%`,
        time,
        worker: worker.name,
      })
    }
    if (worker.status === 'stopped') {
      alerts.push({
        id: `offline-${worker.id}`,
        type: 'error',
        title: 'Worker 离线',
        message: `${worker.name} Worker 当前处于离线状态`,
        time,
        worker: worker.name,
      })
    }
  })
  return alerts
}

const addUsageAlert = (
  alerts: MonitorAlert[],
  worker: WorkerDisplayData,
  field: 'cpu' | 'memory',
  label: 'CPU' | '内存',
  time: string,
) => {
  const value = worker[field]
  if (value > 85) {
    alerts.push({
      id: `${field === 'cpu' ? 'cpu' : 'mem'}-${worker.id}`,
      type: 'error',
      title: `${label}${label === '内存' ? '资源不足' : '使用率过高'}`,
      message: `${worker.name} Worker${label}使用率超过85%，当前${value}%`,
      time,
      worker: worker.name,
    })
  } else if (value > 70) {
    alerts.push({
      id: `${field === 'cpu' ? 'cpu-warn' : 'mem-warn'}-${worker.id}`,
      type: 'warning',
      title: `${label}使用率较高`,
      message: `${worker.name} Worker${label}使用率${value}%，建议关注`,
      time,
      worker: worker.name,
    })
  }
}

export const calculateMonitorStats = (workers: WorkerDisplayData[]): MonitorStats => {
  const warningCount = workers.filter((worker) => worker.status === 'warning').length
  const errorCount = workers.filter((worker) => worker.status === 'error' || worker.status === 'stopped').length
  return {
    totalWorkers: workers.length,
    onlineCount: workers.filter((worker) => worker.status === 'running').length,
    warningCount,
    errorCount,
    totalTasks: workers.reduce((sum, worker) => sum + worker.tasks, 0),
    systemStatus: errorCount > 0 ? 'error' : warningCount > 0 ? 'warning' : 'normal',
  }
}

export const getPerformancePeriodHours = (period: PerformancePeriod): number => {
  if (period === '7d') return 24 * 7
  if (period === '30d') return 24 * 30
  return 24
}

export const formatTimeLabel = (timestamp: string, period: PerformancePeriod): string => {
  const date = new Date(timestamp)
  if (period === '24h') return `${date.getHours()}:00`
  if (period === '7d') return `${date.getMonth() + 1}/${date.getDate()} ${date.getHours()}:00`
  return `${date.getMonth() + 1}/${date.getDate()}`
}
