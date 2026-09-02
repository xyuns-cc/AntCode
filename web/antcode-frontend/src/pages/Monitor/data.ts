import type { Worker } from '@/types'
import type {
  MonitorAlert,
  MonitorStats,
  PerformancePeriod,
  WorkerDisplayData,
  WorkerOs,
} from './types'

/**
 * 「上次检查」文案。
 *
 * 取的是最后一次**拉取成功**的时刻：后台每 10 秒轮询一次，失败时不推进该时刻，
 * 所以后端挂掉时这里会如实变老，而不是继续报「刚刚」。`null` = 一次都没成功过，
 * 不能当成「刚刚检查过」。
 */
export const describeLastChecked = (lastSuccessAt: number | null, now: number): string => {
  if (lastSuccessAt === null) return '尚未成功获取'
  const minutes = Math.floor(Math.max(0, now - lastSuccessAt) / 60_000)
  if (minutes < 1) return '刚刚'
  return minutes >= 10 ? '10分钟前' : `${minutes}分钟前`
}

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

// Worker 上报的使用率是 float，直接插进文案会出现「磁盘使用率85.9000015258789%」
// 这种二进制浮点残渣。只格式化文案，不动 worker.cpu/memory/disk 本身——阈值判定
// 和图表条形都读原值，提前取整会挪动 getDisplayStatus 的边界。
const USAGE_DECIMALS = 1

export const formatUsagePercent = (value: number): string =>
  `${Number.parseFloat(value.toFixed(USAGE_DECIMALS))}`

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
        message: `${worker.name} Worker磁盘使用率${formatUsagePercent(worker.disk)}%`,
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
      message: `${worker.name} Worker${label}使用率超过85%，当前${formatUsagePercent(value)}%`,
      time,
      worker: worker.name,
    })
  } else if (value > 70) {
    alerts.push({
      id: `${field === 'cpu' ? 'cpu-warn' : 'mem-warn'}-${worker.id}`,
      type: 'warning',
      title: `${label}使用率较高`,
      message: `${worker.name} Worker${label}使用率${formatUsagePercent(value)}%，建议关注`,
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
