/* eslint-disable @typescript-eslint/ban-ts-comment */
// @ts-nocheck
import React, { useEffect, useState, useMemo, useCallback } from 'react'
import { Card, Row, Col, Progress, Tag, Badge, Button, Drawer, Descriptions, Space, Tooltip, message, theme } from 'antd'
import {
  SyncOutlined,
  CheckCircleOutlined,
  WarningOutlined,
  CloseCircleOutlined,
  ThunderboltOutlined,
  DatabaseOutlined,
  HddOutlined,
  CloudServerOutlined,
  BugOutlined,
  ClockCircleOutlined,
  RightOutlined,
  WindowsOutlined,
  AppleOutlined,
  LinuxOutlined
} from '@ant-design/icons'
import ResponsiveTable from '@/components/common/ResponsiveTable'
import {
  Chart as ChartJS,
  CategoryScale,
  LinearScale,
  PointElement,
  LineElement,
  BarElement,
  Title,
  Tooltip as ChartTooltip,
  Legend,
  Filler
} from 'chart.js'
import zoomPlugin from 'chartjs-plugin-zoom'
import { Line, Bar } from 'react-chartjs-2'
import { workerService } from '@/services/workers'
import { taskService } from '@/services/tasks'
import type { Worker, WorkerAggregateStats } from '@/types'
import './monitor.css'

// 注册 Chart.js 组件
ChartJS.register(
  CategoryScale,
  LinearScale,
  PointElement,
  LineElement,
  BarElement,
  Title,
  ChartTooltip,
  Legend,
  Filler,
  zoomPlugin
)

// Worker显示数据类型
interface WorkerDisplayData {
  id: string
  name: string
  version: string
  os: 'windows' | 'ubuntu' | 'debian' | 'centos' | 'redhat' | 'alpine' | 'fedora' | 'macos' | 'linux'
  status: 'running' | 'warning' | 'error' | 'stopped'
  cpu: number
  memory: number
  disk: number
  tasks: number
  uptime: string
  host: string
  port: number
  lastHeartbeat?: string
  // 详细资源信息
  cpuCores?: number
  memoryTotal?: number
  memoryUsed?: number
  memoryAvailable?: number
  diskTotal?: number
  diskUsed?: number
  diskFree?: number
}

interface Alert {
  id: string
  type: 'error' | 'warning' | 'info'
  title: string
  message: string
  time: string
  worker: string
}

interface Task {
  id: string
  name: string
  worker: string
  status: 'running' | 'success' | 'failed' | 'pending'
  cpu: number | string
  memory: number | string
  duration: string
}

interface WorkerLog {
  id: string
  worker: string
  type: 'error' | 'warning' | 'info' | 'success'
  message: string
  time: string
}

// 计算性能趋势的小时数（组件外部工具函数）
const getPerformancePeriodHours = (period: '24h' | '7d' | '30d'): number => {
  switch (period) {
    case '24h': return 24
    case '7d': return 24 * 7
    case '30d': return 24 * 30
    default: return 24
  }
}

const Monitor: React.FC = () => {
  const { token } = theme.useToken()
  const [loading, setLoading] = useState(false)
  const [currentTime, setCurrentTime] = useState(new Date())
  const [isLargeScreen, _setIsLargeScreen] = useState(false)
  const [showAllWorkers, setShowAllWorkers] = useState(false)
  const [showAllAlerts, setShowAllAlerts] = useState(false)
  const [selectedWorker, setSelectedWorker] = useState<WorkerDisplayData | null>(null)
  const chartRef = React.useRef<ChartJS | null>(null)

  // 真实Worker 数据
  const [workers, setWorkers] = useState<WorkerDisplayData[]>([])
  const [, setWorkerStats] = useState<WorkerAggregateStats | null>(null)
  const [lastChecked, setLastChecked] = useState<string>('刚刚')

  // 将 API Worker 转换为显示数据
  const transformWorker = useCallback((worker: Worker): WorkerDisplayData => {
    // 根据状态和指标判断显示状态
    let displayStatus: 'running' | 'warning' | 'error' | 'stopped' = 'stopped'
    if (worker.status === 'online') {
      const cpu = worker.metrics?.cpu || 0
      const memory = worker.metrics?.memory || 0
      if (cpu > 90 || memory > 90) {
        displayStatus = 'error'
      } else if (cpu > 75 || memory > 75) {
        displayStatus = 'warning'
      } else {
        displayStatus = 'running'
      }
    } else if (worker.status === 'maintenance') {
      displayStatus = 'warning'
    } else if (worker.status === 'connecting') {
      displayStatus = 'warning'
    }

    // 格式化运行时间
    const formatUptime = (seconds?: number): string => {
      if (!seconds) return '未知'
      const days = Math.floor(seconds / 86400)
      const hours = Math.floor((seconds % 86400) / 3600)
      if (days > 0) return `${days}天 ${hours}小时`
      const minutes = Math.floor((seconds % 3600) / 60)
      if (hours > 0) return `${hours}小时 ${minutes}分钟`
      return `${minutes}分钟`
    }

    // 映射操作系统类型
    const mapOsType = (osType?: string): WorkerDisplayData['os'] => {
      if (!osType) return 'linux'
      const osLower = osType.toLowerCase()
      if (osLower === 'darwin' || osLower === 'macos') return 'macos'
      if (osLower === 'windows') return 'windows'
      if (osLower.includes('ubuntu')) return 'ubuntu'
      if (osLower.includes('debian')) return 'debian'
      if (osLower.includes('centos')) return 'centos'
      if (osLower.includes('redhat') || osLower.includes('rhel')) return 'redhat'
      if (osLower.includes('alpine')) return 'alpine'
      if (osLower.includes('fedora')) return 'fedora'
      return 'linux'
    }

    return {
      id: worker.id,
      name: worker.name,
      version: worker.version || 'v1.0.0',
      os: mapOsType(worker.osType),
      status: displayStatus,
      cpu: worker.metrics?.cpu || 0,
      memory: worker.metrics?.memory || 0,
      disk: worker.metrics?.disk || 0,
      tasks: worker.metrics?.runningTasks || 0,
      uptime: formatUptime(worker.metrics?.uptime),
      host: worker.host,
      port: worker.port,
      lastHeartbeat: worker.lastHeartbeat,
      // 详细资源信息
      cpuCores: worker.metrics?.cpuCores,
      memoryTotal: worker.metrics?.memoryTotal,
      memoryUsed: worker.metrics?.memoryUsed,
      memoryAvailable: worker.metrics?.memoryAvailable,
      diskTotal: worker.metrics?.diskTotal,
      diskUsed: worker.metrics?.diskUsed,
      diskFree: worker.metrics?.diskFree,
    }
  }, [])

  // 加载Worker 数据
  const loadWorkers = useCallback(async (showLoading = true) => {
    if (showLoading) setLoading(true)
    try {
      const [allWorkers, stats] = await Promise.all([
        workerService.getAllWorkers(),
        workerService.getAggregateStats().catch(() => null)
      ])
      setWorkers(allWorkers.map(transformWorker))
      if (stats) setWorkerStats(stats)
      setLastChecked('刚刚')
    } catch (error) {
      console.error('加载Worker 数据失败:', error)
      if (showLoading) message.error('加载Worker 数据失败')
    } finally {
      if (showLoading) setLoading(false)
    }
  }, [transformWorker])

  // 初始加载
  useEffect(() => {
    loadWorkers()
  }, [loadWorkers])

  // 定时刷新（每 10 秒）
  useEffect(() => {
    const interval = setInterval(() => {
      loadWorkers(false) // 静默刷新
      setLastChecked('刚刚')
    }, 10000)
    return () => clearInterval(interval)
  }, [loadWorkers])

  // 更新最后检查时间
  useEffect(() => {
    const interval = setInterval(() => {
      setLastChecked(prev => {
        if (prev === '刚刚') return '1分钟前'
        const match = prev.match(/(\d+)分钟前/)
        if (match) {
          const minutes = parseInt(match[1]) + 1
          if (minutes >= 10) return '10分钟前'
          return `${minutes}分钟前`
        }
        return prev
      })
    }, 60000)
    return () => clearInterval(interval)
  }, [])

  // 根据Worker 状态生成告警数据
  const alerts = useMemo<Alert[]>(() => {
    const alertList: Alert[] = []
    workers.forEach(worker => {
      if (worker.cpu > 85) {
        alertList.push({
          id: `cpu-${worker.id}`,
          type: 'error',
          title: 'CPU使用率过高',
          message: `${worker.name} WorkerCPU使用率超过85%，当前${worker.cpu}%`,
          time: lastChecked,
          worker: worker.name
        })
      } else if (worker.cpu > 70) {
        alertList.push({
          id: `cpu-warn-${worker.id}`,
          type: 'warning',
          title: 'CPU使用率较高',
          message: `${worker.name} WorkerCPU使用率${worker.cpu}%，建议关注`,
          time: lastChecked,
          worker: worker.name
        })
      }
      if (worker.memory > 85) {
        alertList.push({
          id: `mem-${worker.id}`,
          type: 'error',
          title: '内存资源不足',
          message: `${worker.name} Worker内存使用率超过85%，当前${worker.memory}%`,
          time: lastChecked,
          worker: worker.name
        })
      } else if (worker.memory > 70) {
        alertList.push({
          id: `mem-warn-${worker.id}`,
          type: 'warning',
          title: '内存使用率较高',
          message: `${worker.name} Worker内存使用率${worker.memory}%，建议关注`,
          time: lastChecked,
          worker: worker.name
        })
      }
      if (worker.disk > 80) {
        alertList.push({
          id: `disk-${worker.id}`,
          type: 'warning',
          title: '磁盘空间不足',
          message: `${worker.name} Worker磁盘使用率${worker.disk}%`,
          time: lastChecked,
          worker: worker.name
        })
      }
      if (worker.status === 'stopped') {
        alertList.push({
          id: `offline-${worker.id}`,
          type: 'error',
          title: 'Worker 离线',
          message: `${worker.name} Worker 当前处于离线状态`,
          time: lastChecked,
          worker: worker.name
        })
      }
    })
    return alertList
  }, [workers, lastChecked])

  // 卡片中显示的告警（最多5条）
  const displayAlerts = useMemo(() => alerts.slice(0, 5), [alerts])

  // 任务数据 - 从各Worker汇总
  const [tasks, setTasks] = useState<Task[]>([])
  
  // 加载任务数据
  useEffect(() => {
    const loadTasks = async () => {
      try {
        const response = await taskService.getTasks({ page: 1, size: 20 })
        const taskList = response.items || []
        setTasks(taskList.map((t: { id: string; name: string; specified_worker_id?: string; status: string; last_run_duration?: number }) => ({
          id: t.id,
          name: t.name,
          worker: workers.find(n => n.id === t.specified_worker_id)?.name || '未分配',
          status: t.status === 'running' ? 'running' : t.status === 'failed' ? 'failed' : t.status === 'success' ? 'success' : 'pending',
          cpu: '-', // 任务级别的 CPU/内存暂不支持
          memory: '-',
          duration: t.last_run_duration ? `${Math.round(t.last_run_duration)}秒` : '-'
        })))
      } catch (error) {
        console.error('加载任务失败:', error)
      }
    }
    loadTasks()
  }, [workers])

  // Worker 日志数据 - 根据Worker 状态动态生成
  const workerLogs = useMemo<WorkerLog[]>(() => {
    const logs: WorkerLog[] = []
    workers.forEach(worker => {
      if (worker.status === 'running') {
        logs.push({
          id: `health-${worker.id}`,
          worker: worker.name,
          type: 'success',
          message: '系统健康检查通过',
          time: lastChecked
        })
      }
      if (worker.cpu > 70) {
        logs.push({
          id: `cpu-log-${worker.id}`,
          worker: worker.name,
          type: worker.cpu > 85 ? 'error' : 'warning',
          message: `CPU使用率 ${worker.cpu}%`,
          time: lastChecked
        })
      }
      if (worker.memory > 70) {
        logs.push({
          id: `mem-log-${worker.id}`,
          worker: worker.name,
          type: worker.memory > 85 ? 'error' : 'warning',
          message: `内存使用率 ${worker.memory}%`,
          time: lastChecked
        })
      }
      if (worker.status === 'stopped') {
        logs.push({
          id: `offline-log-${worker.id}`,
          worker: worker.name,
          type: 'error',
          message: 'Worker 离线，请检查网络连接',
          time: lastChecked
        })
      }
      if (worker.tasks > 0) {
        logs.push({
          id: `task-log-${worker.id}`,
          worker: worker.name,
          type: 'info',
          message: `当前运行 ${worker.tasks} 个任务`,
          time: lastChecked
        })
      }
    })
    return logs
  }, [workers, lastChecked])

  // 生成过去24小时的时间标签
  const _generateTimeLabels = useCallback(() => {
    const labels = []
    const now = new Date()
    for (let i = 23; i >= 0; i--) {
      const hour = new Date(now.getTime() - i * 60 * 60 * 1000)
      labels.push(`${hour.getHours()}:00`)
    }
    return labels
  }, [])

  // 性能趋势时间范围状态
  const [performancePeriod, setPerformancePeriod] = useState<'24h' | '7d' | '30d'>('24h')

  // 集群历史指标数据
  const [clusterHistory, setClusterHistory] = useState<{
    timestamps: string[]
    cpu: { avg: number[]; max: number[]; min: number[] }
    memory: { avg: number[]; max: number[]; min: number[] }
  } | null>(null)

  // Worker 详情历史指标
  const [workerHistory, setWorkerHistory] = useState<Array<{
    timestamp: string
    cpu: number
    memory: number
    disk: number
  }>>([])

  // 加载集群历史指标
  const loadClusterHistory = useCallback(async () => {
    try {
      const hours = getPerformancePeriodHours(performancePeriod)
      const history = await workerService.getClusterMetricsHistory(hours)
      setClusterHistory(history)
    } catch (error) {
      console.error('加载集群历史指标失败:', error)
      // 静默失败，保持之前的数据
    }
  }, [performancePeriod])

  // 加载Worker 详情历史指标
  const loadWorkerHistory = useCallback(async (workerId: string) => {
    try {
      const history = await workerService.getWorkerMetricsHistory(workerId, 720) // 30天
      setWorkerHistory(history)
    } catch (error) {
      console.error('加载Worker历史指标失败:', error)
      setWorkerHistory([])
    }
  }, [])

  // 初始加载集群历史
  useEffect(() => {
    loadClusterHistory()
  }, [loadClusterHistory])

  // 定时刷新集群历史（每分钟）
  useEffect(() => {
    const interval = setInterval(loadClusterHistory, 60000)
    return () => clearInterval(interval)
  }, [loadClusterHistory])

  // 选中Worker时加载历史，并设置定时刷新
  useEffect(() => {
    if (!selectedWorker) return
    
    // 立即加载一次
    loadWorkerHistory(selectedWorker.id)
    
    // 设置定时刷新（每30秒）
    const interval = setInterval(() => {
      loadWorkerHistory(selectedWorker.id)
    }, 30000)
    
    // 清理函数
    return () => clearInterval(interval)
  }, [selectedWorker, loadWorkerHistory])

  // 格式化时间标签
  const formatTimeLabel = useCallback((timestamp: string) => {
    const date = new Date(timestamp)
    if (performancePeriod === '24h') {
      return `${date.getHours()}:00`
    } else if (performancePeriod === '7d') {
      return `${date.getMonth() + 1}/${date.getDate()} ${date.getHours()}:00`
    } else {
      return `${date.getMonth() + 1}/${date.getDate()}`
    }
  }, [performancePeriod])

  // Worker 详情图表数据 - 使用真实历史数据（使用 useMemo 优化性能）
  const workerDetailChartData = useMemo(() => {
    if (!selectedWorker) return null
    
    // 如果有历史数据，使用真实数据
    if (workerHistory.length > 0) {
      const labels = workerHistory.map(h => {
        const date = new Date(h.timestamp)
        return `${date.getMonth() + 1}/${date.getDate()} ${date.getHours()}:00`
      })
      
      return {
        labels,
        datasets: [
          {
            label: 'CPU',
            data: workerHistory.map(h => h.cpu),
            borderColor: '#1890ff',
            backgroundColor: 'rgba(24, 144, 255, 0.1)',
            tension: 0.4,
            fill: true,
            borderWidth: 2.5,
            pointRadius: 1,
            pointHoverRadius: 6,
          },
          {
            label: '内存',
            data: workerHistory.map(h => h.memory),
            borderColor: '#52c41a',
            backgroundColor: 'rgba(82, 196, 26, 0.1)',
            tension: 0.4,
            fill: true,
            borderWidth: 2.5,
            pointRadius: 1,
            pointHoverRadius: 6,
          },
        ],
      }
    }
    
    // 没有历史数据时，显示当前值的单点
    return {
      labels: ['当前'],
      datasets: [
        {
          label: 'CPU',
          data: [selectedWorker.cpu],
          borderColor: '#1890ff',
          backgroundColor: 'rgba(24, 144, 255, 0.1)',
          tension: 0.4,
          fill: true,
          borderWidth: 2.5,
          pointRadius: 5,
        },
        {
          label: '内存',
          data: [selectedWorker.memory],
          borderColor: '#52c41a',
          backgroundColor: 'rgba(82, 196, 26, 0.1)',
          tension: 0.4,
          fill: true,
          borderWidth: 2.5,
          pointRadius: 5,
        },
      ],
    }
  }, [selectedWorker, workerHistory])

  // 计算当前集群资源使用情况（用于显示当前值）
  const clusterMetrics = useMemo(() => {
    if (workers.length === 0) return { avgCpu: 0, avgMem: 0, maxCpu: 0, maxMem: 0, minCpu: 0, minMem: 0 }
    const cpuValues = workers.map(n => n.cpu)
    const memValues = workers.map(n => n.memory)
    return {
      avgCpu: Math.round(cpuValues.reduce((a, b) => a + b, 0) / workers.length),
      avgMem: Math.round(memValues.reduce((a, b) => a + b, 0) / workers.length),
      maxCpu: Math.max(...cpuValues),
      maxMem: Math.max(...memValues),
      minCpu: Math.min(...cpuValues),
      minMem: Math.min(...memValues),
    }
  }, [workers])

  // CPU趋势数据 - 使用真实历史数据
  const cpuTrendData = useMemo(() => {
    // 使用真实历史数据
    if (clusterHistory && clusterHistory.timestamps.length > 0) {
      return {
        labels: clusterHistory.timestamps.map(formatTimeLabel),
        datasets: [
          {
            label: '平均',
            data: clusterHistory.cpu.avg,
            borderColor: '#1890ff',
            backgroundColor: 'rgba(24, 144, 255, 0.1)',
            tension: 0.4,
            fill: true,
            borderWidth: 3,
            pointRadius: 0,
            pointHoverRadius: 6,
          },
          {
            label: '最大',
            data: clusterHistory.cpu.max,
            borderColor: '#ff7875',
            backgroundColor: 'transparent',
            tension: 0.4,
            fill: false,
            borderWidth: 2,
            borderDash: [8, 4],
            pointRadius: 0,
            pointHoverRadius: 5,
          },
          {
            label: '最小',
            data: clusterHistory.cpu.min,
            borderColor: '#95de64',
            backgroundColor: 'transparent',
            tension: 0.4,
            fill: false,
            borderWidth: 2,
            borderDash: [8, 4],
            pointRadius: 0,
            pointHoverRadius: 5,
          },
        ],
      }
    }
    
    // 没有历史数据时显示当前快照
    return {
      labels: ['当前'],
      datasets: [
        {
          label: '平均',
          data: [clusterMetrics.avgCpu],
          borderColor: '#1890ff',
          backgroundColor: 'rgba(24, 144, 255, 0.1)',
          borderWidth: 3,
          pointRadius: 5,
        },
        {
          label: '最大',
          data: [clusterMetrics.maxCpu],
          borderColor: '#ff7875',
          borderWidth: 2,
          pointRadius: 5,
        },
        {
          label: '最小',
          data: [clusterMetrics.minCpu],
          borderColor: '#95de64',
          borderWidth: 2,
          pointRadius: 5,
        },
      ],
    }
  }, [clusterHistory, clusterMetrics, formatTimeLabel])

  // 内存趋势数据 - 使用真实历史数据
  const memoryTrendData = useMemo(() => {
    // 使用真实历史数据
    if (clusterHistory && clusterHistory.timestamps.length > 0) {
      return {
        labels: clusterHistory.timestamps.map(formatTimeLabel),
        datasets: [
          {
            label: '平均',
            data: clusterHistory.memory.avg,
            borderColor: '#722ed1',
            backgroundColor: 'rgba(114, 46, 209, 0.1)',
            tension: 0.4,
            fill: true,
            borderWidth: 3,
            pointRadius: 0,
            pointHoverRadius: 6,
          },
          {
            label: '最大',
            data: clusterHistory.memory.max,
            borderColor: '#ff7875',
            backgroundColor: 'transparent',
            tension: 0.4,
            fill: false,
            borderWidth: 2,
            borderDash: [8, 4],
            pointRadius: 0,
            pointHoverRadius: 5,
          },
          {
            label: '最小',
            data: clusterHistory.memory.min,
            borderColor: '#95de64',
            backgroundColor: 'transparent',
            tension: 0.4,
            fill: false,
            borderWidth: 2,
            borderDash: [8, 4],
            pointRadius: 0,
            pointHoverRadius: 5,
          },
        ],
      }
    }
    
    // 没有历史数据时显示当前快照
    return {
      labels: ['当前'],
      datasets: [
        {
          label: '平均',
          data: [clusterMetrics.avgMem],
          borderColor: '#722ed1',
          backgroundColor: 'rgba(114, 46, 209, 0.1)',
          borderWidth: 3,
          pointRadius: 5,
        },
        {
          label: '最大',
          data: [clusterMetrics.maxMem],
          borderColor: '#ff7875',
          borderWidth: 2,
          pointRadius: 5,
        },
        {
          label: '最小',
          data: [clusterMetrics.minMem],
          borderColor: '#95de64',
          borderWidth: 2,
          pointRadius: 5,
        },
      ],
    }
  }, [clusterHistory, clusterMetrics, formatTimeLabel])

  // 任务执行统计数据 - 使用真实数据
  const taskStatsData = useMemo(() => {
    const successCount = tasks.filter(t => t.status === 'success').length
    const failedCount = tasks.filter(t => t.status === 'failed').length
    const runningCount = tasks.filter(t => t.status === 'running').length
    const pendingCount = tasks.filter(t => t.status === 'pending').length
    
    return {
      labels: ['成功', '失败', '运行中', '待执行'],
      datasets: [
        {
          label: '任务数量',
          data: [successCount, failedCount, runningCount, pendingCount],
          backgroundColor: ['#52c41a', '#ff4d4f', '#1890ff', '#faad14'],
        },
      ],
    }
  }, [tasks])

  // 磁盘使用数据 - 使用真实Worker磁盘数据
  const diskUsageData = useMemo(() => {
    if (workers.length === 0) {
      return {
        labels: ['暂无数据'],
        datasets: [{
          label: '磁盘使用率',
          data: [0],
          backgroundColor: ['#d9d9d9'],
        }],
      }
    }
    
    return {
      labels: workers.map(n => n.name),
      datasets: [{
        label: '磁盘使用率 (%)',
        data: workers.map(n => n.disk),
        backgroundColor: workers.map(n => 
          n.disk > 80 ? '#ff4d4f' : n.disk > 60 ? '#faad14' : '#722ed1'
        ),
      }],
    }
  }, [workers])

  const chartOptions = {
    responsive: true,
    maintainAspectRatio: false,
    interaction: {
      mode: 'index' as const,
      intersect: false,
    },
    plugins: {
      legend: {
        position: 'top' as const,
        align: 'end' as const,
        labels: {
          font: { size: 11, weight: '500' },
          usePointStyle: true,
          pointStyle: 'circle',
          padding: 12,
          color: token.colorTextSecondary,
        },
      },
      tooltip: {
        enabled: true,
        mode: 'index' as const,
        intersect: false,
        backgroundColor: 'rgba(0, 0, 0, 0.8)',
        titleColor: '#fff',
        bodyColor: '#fff',
        borderColor: 'rgba(255, 255, 255, 0.2)',
        borderWidth: 1,
        padding: 10,
        displayColors: true,
        callbacks: {
          label: function(context: { dataset: { label?: string }; parsed: { y: number | null } }) {
            let label = context.dataset.label || ''
            if (label) {
              label += ': '
            }
            if (context.parsed.y !== null) {
              label += context.parsed.y + '%'
            }
            return label
          }
        }
      },
      zoom: undefined, // 主页面图表不启用缩放
    },
    scales: {
      y: {
        beginAtZero: true,
        max: 100,
        ticks: {
          callback: function(value: string | number) {
            return value + '%'
          },
          font: {
            size: 11
          },
          color: token.colorTextTertiary,
        },
        grid: {
          color: token.colorBorderSecondary,
          drawBorder: false,
        },
        border: {
          display: false,
        },
      },
      x: {
        ticks: {
          maxRotation: 0,
          autoSkip: true,
          maxTicksLimit: 8,
          font: {
            size: 10
          },
          color: token.colorTextTertiary,
        },
        grid: {
          display: false,
        },
        border: {
          display: false,
        },
      },
    },
  }

  // Worker 详情图表配置 - 支持缩放和滚动（使用 useMemo 避免重新创建）
  const workerDetailChartOptions = useMemo(() => ({
    responsive: true,
    maintainAspectRatio: false,
    interaction: {
      mode: 'index' as const,
      intersect: false,
    },
    plugins: {
      legend: {
        position: 'top' as const,
        align: 'end' as const,
        labels: {
          font: { size: 11, weight: '500' },
          usePointStyle: true,
          pointStyle: 'circle',
          padding: 12,
          color: token.colorTextSecondary,
        },
      },
      tooltip: {
        enabled: true,
        mode: 'index' as const,
        intersect: false,
        backgroundColor: 'rgba(0, 0, 0, 0.8)',
        titleColor: '#fff',
        bodyColor: '#fff',
        borderColor: 'rgba(255, 255, 255, 0.2)',
        borderWidth: 1,
        padding: 10,
        displayColors: true,
        callbacks: {
          label: function(context: { dataset: { label?: string }; parsed: { y: number | null } }) {
            let label = context.dataset.label || ''
            if (label) {
              label += ': '
            }
            if (context.parsed.y !== null) {
              label += context.parsed.y + '%'
            }
            return label
          }
        }
      },
      zoom: {
        pan: {
          enabled: true,
          mode: 'x' as const,
        },
        zoom: {
          wheel: {
            enabled: true,
          },
          pinch: {
            enabled: true,
          },
          mode: 'x' as const,
        },
      },
    },
    scales: {
      y: {
        beginAtZero: true,
        max: 100,
        ticks: {
          callback: function(value: string | number) {
            return value + '%'
          },
          font: {
            size: 11
          },
          color: token.colorTextTertiary,
        },
        grid: {
          color: token.colorBorderSecondary,
          drawBorder: false,
        },
        border: {
          display: false,
        },
      },
      x: {
        ticks: {
          maxRotation: 0,
          autoSkip: true,
          font: {
            size: 10
          },
          color: token.colorTextTertiary,
        },
        grid: {
          display: false,
        },
        border: {
          display: false,
        },
      },
    },
  }), [token])

  // 更新时间
  useEffect(() => {
    const timer = setInterval(() => {
      setCurrentTime(new Date())
    }, 1000)
    return () => clearInterval(timer)
  }, [])

  // 刷新数据
  const handleRefresh = () => {
    loadWorkers(true)
    message.success('数据刷新成功')
  }

  // 计算统计数据
  const statsData = useMemo(() => {
    const onlineCount = workers.filter(n => n.status === 'running').length
    const warningCount = workers.filter(n => n.status === 'warning').length
    const errorCount = workers.filter(n => n.status === 'error' || n.status === 'stopped').length
    const totalTasks = workers.reduce((sum, n) => sum + n.tasks, 0)
    
    return {
      totalWorkers: workers.length,
      onlineCount,
      warningCount,
      errorCount,
      totalTasks,
      systemStatus: errorCount > 0 ? 'error' : warningCount > 0 ? 'warning' : 'normal'
    }
  }, [workers])

  // 获取状态颜色
  const getStatusColor = (status: string) => {
    switch (status) {
      case 'running': return 'success'
      case 'warning': return 'warning'
      case 'error': return 'error'
      case 'stopped': return 'default'
      case 'success': return 'success'
      case 'failed': return 'error'
      case 'pending': return 'processing'
      default: return 'default'
    }
  }

  // 获取状态文本
  const getStatusText = (status: string) => {
    switch (status) {
      case 'running': return '运行中'
      case 'warning': return '需注意'
      case 'error': return '异常'
      case 'stopped': return '已停止'
      case 'success': return '成功'
      case 'failed': return '失败'
      case 'pending': return '待执行'
      default: return '未知'
    }
  }

  // 获取操作系统图标
  const getOsIcon = (os: string) => {
    switch (os) {
      case 'windows':
        return <WindowsOutlined style={{ fontSize: 14, marginRight: 4, color: token.colorInfo }} />
      case 'ubuntu':
      case 'debian':
      case 'centos':
      case 'redhat':
      case 'alpine':
      case 'fedora':
      case 'linux':
        return <LinuxOutlined style={{ fontSize: 14, marginRight: 4, color: token.colorWarning }} />
      case 'macos':
        return <AppleOutlined style={{ fontSize: 14, marginRight: 4, color: token.colorTextSecondary }} />
      default:
        return <CloudServerOutlined style={{ fontSize: 14, marginRight: 4, color: token.colorPrimary }} />
    }
  }

  // 获取操作系统名称
  const getOsName = (os: string): string => {
    switch (os) {
      case 'windows': return 'Windows Server'
      case 'ubuntu': return 'Ubuntu'
      case 'debian': return 'Debian'
      case 'centos': return 'CentOS'
      case 'redhat': return 'Red Hat'
      case 'alpine': return 'Alpine Linux'
      case 'fedora': return 'Fedora'
      case 'macos': return 'macOS'
      case 'linux': return 'Linux'
      default: return 'Worker'
    }
  }

  // 获取告警图标
  const getAlertIcon = (type: string) => {
    switch (type) {
      case 'error': return <CloseCircleOutlined style={{ color: token.colorError }} />
      case 'warning': return <WarningOutlined style={{ color: token.colorWarning }} />
      case 'info': return <CheckCircleOutlined style={{ color: token.colorInfo }} />
      default: return null
    }
  }

  // 获取日志类型标签颜色
  const getLogTypeColor = (type: string): string => {
    switch (type) {
      case 'error': return 'error'
      case 'warning': return 'warning'
      case 'info': return 'default'
      case 'success': return 'success'
      default: return 'default'
    }
  }

  // 获取日志类型文本
  const getLogTypeText = (type: string): string => {
    switch (type) {
      case 'error': return '错误'
      case 'warning': return '警告'
      case 'info': return '信息'
      case 'success': return '成功'
      default: return '未知'
    }
  }

  return (
    <div className={`monitor-container ${isLargeScreen ? 'large-screen' : ''}`}>
      {/* 简化的头部区域 */}
      <div className="monitor-header-simple">
        <div className="header-left">
          <h2 style={{ margin: 0, fontSize: '18px', fontWeight: 600 }}>
            <CloudServerOutlined style={{ marginRight: 8, color: token.colorPrimary }} />
            Worker监控
          </h2>
          <div className="header-badges">
            <Badge 
              status={statsData.systemStatus === 'error' ? 'error' : statsData.systemStatus === 'warning' ? 'warning' : 'success'} 
              text={statsData.systemStatus === 'error' ? '系统异常' : statsData.systemStatus === 'warning' ? '需要关注' : '系统正常'} 
            />
            <span style={{ fontSize: 12, color: token.colorTextSecondary, marginLeft: 16 }}>
              <ClockCircleOutlined /> {currentTime.toLocaleString('zh-CN')}
            </span>
          </div>
        </div>
        <Button
          icon={<SyncOutlined spin={loading} />}
          onClick={handleRefresh}
          loading={loading}
          size="small"
        >
          刷新
        </Button>
      </div>

      {/* 快速统计 */}
      <Row gutter={[12, 12]} style={{ marginBottom: 16 }}>
        <Col xs={12} sm={6}>
          <div className="mini-stat-card">
            <div className="stat-decor" style={{ background: `${token.colorPrimary}10` }} />
            <div className="stat-icon" style={{ background: `${token.colorPrimary}20`, color: token.colorPrimary }}>
              <CloudServerOutlined />
            </div>
            <div className="stat-content">
              <div className="stat-label">执行 Worker</div>
              <div className="stat-value">{statsData.totalWorkers}</div>
            </div>
          </div>
        </Col>
        <Col xs={12} sm={6}>
          <div className="mini-stat-card">
            <div className="stat-decor" style={{ background: `${token.colorInfo}10` }} />
            <div className="stat-icon" style={{ background: `${token.colorInfo}20`, color: token.colorInfo }}>
              <ThunderboltOutlined />
            </div>
            <div className="stat-content">
              <div className="stat-label">运行任务</div>
              <div className="stat-value">{statsData.totalTasks}</div>
            </div>
          </div>
        </Col>
        <Col xs={12} sm={6}>
          <div className="mini-stat-card">
            <div className="stat-decor" style={{ background: `${token.colorWarning}10` }} />
            <div className="stat-icon" style={{ background: `${token.colorWarning}20`, color: token.colorWarning }}>
              <WarningOutlined />
            </div>
            <div className="stat-content">
              <div className="stat-label">警告</div>
              <div className="stat-value">{statsData.warningCount}</div>
            </div>
          </div>
        </Col>
        <Col xs={12} sm={6}>
          <div className="mini-stat-card">
            <div className="stat-decor" style={{ background: `${token.colorError}10` }} />
            <div className="stat-icon" style={{ background: `${token.colorError}20`, color: token.colorError }}>
              <CloseCircleOutlined />
            </div>
            <div className="stat-content">
              <div className="stat-label">错误</div>
              <div className="stat-value">{statsData.errorCount}</div>
            </div>
          </div>
        </Col>
      </Row>

      {/* 主要内容区域 */}
      <div style={{ marginTop: 0 }}>
        {/* 执行 Worker状态 - 水平滚动 */}
        <Card
          size="small"
          title={
            <span style={{ fontSize: 14 }}>
              <CloudServerOutlined /> 执行 Worker状态 ({workers.length}个)
            </span>
          }
          extra={
            <div style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
              <span style={{ fontSize: 11, color: token.colorTextSecondary }}>上次检查: {lastChecked}</span>
              <Button 
                size="small" 
                type="link" 
                onClick={() => setShowAllWorkers(true)}
                style={{ fontSize: 12 }}
              >
                查看全部 <RightOutlined />
              </Button>
            </div>
          }
          style={{ marginBottom: 12 }}
          loading={loading && workers.length === 0}
        >
            <div className="workers-scroll-container">
              {workers.length === 0 && !loading && (
                <div style={{ textAlign: 'center', padding: '40px', color: token.colorTextTertiary, width: '100%' }}>
                  暂无Worker 数据，请先添加Worker
                </div>
              )}
              {workers.map((worker) => (
                <Card 
                  key={worker.id} 
                  className={`worker-card-compact worker-${worker.status}`} 
                  hoverable
                  onClick={() => setSelectedWorker(worker)}
                >
                  <div className="worker-header-compact">
                    <div>
                      <h4>{worker.name}</h4>
                      <p className="worker-version">
                        {getOsIcon(worker.os)} {getOsName(worker.os)} · {worker.version}
                      </p>
                    </div>
                    <Tag color={getStatusColor(worker.status)} style={{ fontSize: 10 }}>
                      {getStatusText(worker.status)}
                    </Tag>
                  </div>
                  <div className="worker-metrics-compact">
                    <div className="metric-row">
                      <span className="metric-label-compact">CPU</span>
                      <div className="metric-value-compact">
                      <Progress
                        percent={Math.round(worker.cpu)}
                        strokeColor={worker.cpu > 80 ? '#ff4d4f' : worker.cpu > 60 ? '#faad14' : '#1890ff'}
                        showInfo={false}
                        size="small"
                        style={{ width: '100%' }}
                      />
                        <span className="metric-percent">{Math.round(worker.cpu)}%</span>
                      </div>
                    </div>
                    <div className="metric-row">
                      <span className="metric-label-compact">内存</span>
                      <div className="metric-value-compact">
                      <Progress
                        percent={Math.round(worker.memory)}
                        strokeColor={worker.memory > 80 ? '#ff4d4f' : worker.memory > 60 ? '#faad14' : '#52c41a'}
                        showInfo={false}
                        size="small"
                        style={{ width: '100%' }}
                      />
                        <span className="metric-percent">{Math.round(worker.memory)}%</span>
                      </div>
                    </div>
                    <div className="metric-item-compact">
                      <div className="metric-label-compact">
                        <span>任务</span>
                        <span>{worker.tasks}个</span>
                      </div>
                      <div className="task-indicators-mini">
                        {Array.from({ length: Math.min(worker.tasks, 4) }).map((_, i) => (
                          <span key={i} className={`indicator-mini indicator-${i % 3 === 0 ? 'success' : i % 3 === 1 ? 'warning' : 'error'}`} />
                        ))}
                        {worker.tasks > 4 && <span className="more-mini">+{worker.tasks - 4}</span>}
                      </div>
                    </div>
                  </div>
                </Card>
              ))}
            </div>
        </Card>

        {/* 第二行：资源告警和性能趋势 */}
        <Row gutter={12} style={{ marginTop: 16, marginBottom: 12 }}>
          {/* 资源告警 */}
          <Col xs={24} lg={12}>
            <Card
              size="small"
              title={
                <span style={{ fontSize: 14 }}>
                  <WarningOutlined /> 资源告警
                  {alerts.length > 0 && (
                    <Badge count={alerts.length} style={{ marginLeft: 8 }} size="small" />
                  )}
                </span>
              }
              extra={alerts.length > 0 && <a style={{ fontSize: 11 }} onClick={() => setShowAllAlerts(true)}>查看全部</a>}
              className="alerts-card"
              styles={{ body: { height: 360, overflow: 'auto', padding: alerts.length === 0 ? '16px' : undefined } }}
            >
            <div className="alerts-list">
              {displayAlerts.length === 0 ? (
                <div style={{ 
                  display: 'flex', 
                  flexDirection: 'column', 
                  alignItems: 'center', 
                  justifyContent: 'center', 
                  height: '100%',
                  color: token.colorTextSecondary 
                }}>
                  <CheckCircleOutlined style={{ fontSize: 40, marginBottom: 12, color: token.colorSuccess }} />
                  <span style={{ fontSize: 13 }}>暂无告警，系统运行正常</span>
                </div>
              ) : (
                displayAlerts.map((alert) => (
                  <div key={alert.id} className={`alert-item-compact alert-${alert.type}`}>
                    <div className="alert-icon-compact">{getAlertIcon(alert.type)}</div>
                    <div className="alert-details-compact">
                      <div className="alert-header-compact">
                        <span className="alert-title-compact">{alert.title}</span>
                        <span className="alert-time-compact">{alert.time}</span>
                      </div>
                      <p className="alert-message-compact">{alert.message}</p>
                    </div>
                  </div>
                ))
              )}
            </div>
          </Card>
        </Col>

          {/* 性能趋势 */}
          <Col xs={24} lg={12}>
            <Card
              size="small"
              title={<span style={{ fontSize: 14 }}><ThunderboltOutlined /> 性能趋势</span>}
              extra={
                <Space.Compact size="small">
                  <Button 
                    type={performancePeriod === '24h' ? 'primary' : 'default'} 
                    size="small"
                    onClick={() => setPerformancePeriod('24h')}
                  >
                    24h
                  </Button>
                  <Button 
                    type={performancePeriod === '7d' ? 'primary' : 'default'} 
                    size="small"
                    onClick={() => setPerformancePeriod('7d')}
                  >
                    7d
                  </Button>
                  <Button 
                    type={performancePeriod === '30d' ? 'primary' : 'default'} 
                    size="small"
                    onClick={() => setPerformancePeriod('30d')}
                  >
                    30d
                  </Button>
                </Space.Compact>
              }
              styles={{ body: { height: 360 } }}
            >
            <div style={{ marginBottom: 16 }}>
              <p style={{ fontSize: 12, marginBottom: 10, color: token.colorTextSecondary, fontWeight: 500 }}>
                集群CPU使用率
              </p>
              <div style={{ 
                height: 160, 
                padding: '8px',
                borderRadius: '6px',
                background: 'rgba(24, 144, 255, 0.02)'
              }}>
                <Line data={cpuTrendData} options={chartOptions} />
              </div>
            </div>
            <div>
              <p style={{ fontSize: 12, marginBottom: 10, color: token.colorTextSecondary, fontWeight: 500 }}>
                集群内存使用率
              </p>
              <div style={{ 
                height: 160, 
                padding: '8px',
                borderRadius: '6px',
                background: 'rgba(114, 46, 209, 0.02)'
              }}>
                <Line data={memoryTrendData} options={chartOptions} />
              </div>
            </div>
          </Card>
          </Col>
        </Row>

        {/* 第三行：任务执行列表和统计 */}
        <Row gutter={12}>
          <Col xs={24} lg={16}>
            <Card
              size="small"
              title={<span style={{ fontSize: 14 }}><BugOutlined /> 关键任务状态</span>}
              extra={
                <Button size="small" icon={<SyncOutlined />} style={{ fontSize: 12 }}>
                  筛选
                </Button>
              }
            >
            <ResponsiveTable
              dataSource={tasks}
              rowKey="id"
              columns={[
                {
                  title: '任务名称',
                  dataIndex: 'name',
                  key: 'name',
                  width: 150,
                  ellipsis: { showTitle: false },
                  render: (name: string) => (
                    <Tooltip title={name} placement="topLeft">
                      <span>{name}</span>
                    </Tooltip>
                  ),
                },
                {
                  title: '执行 Worker',
                  dataIndex: 'worker',
                  key: 'worker',
                  width: 100,
                },
                {
                  title: '状态',
                  dataIndex: 'status',
                  key: 'status',
                  width: 80,
                  render: (status: string) => <Tag color={getStatusColor(status)}>{getStatusText(status)}</Tag>,
                },
                {
                  title: 'CPU',
                  dataIndex: 'cpu',
                  key: 'cpu',
                  width: 60,
                  render: (cpu: number | string) => typeof cpu === 'number' ? `${cpu}%` : cpu,
                },
                {
                  title: '内存',
                  dataIndex: 'memory',
                  key: 'memory',
                  width: 60,
                  render: (memory: number | string) => typeof memory === 'number' ? `${memory}%` : memory,
                },
                {
                  title: '运行时长',
                  dataIndex: 'duration',
                  key: 'duration',
                  width: 90,
                },
                {
                  title: '操作',
                  key: 'action',
                  width: 70,
                  fixed: 'right' as const,
                  render: () => <Button type="link" size="small">详情</Button>,
                },
              ]}
              pagination={{ pageSize: 5, size: 'small' }}
              size="small"
            />
          </Card>
        </Col>

          {/* 任务统计和网络监控 */}
          <Col xs={24} lg={8}>
            <Card
              size="small"
              title={<span style={{ fontSize: 14 }}><DatabaseOutlined /> 任务执行统计</span>}
              style={{ marginBottom: 12 }}
            >
              <div style={{ height: 180 }}>
                <Bar data={taskStatsData} options={{ ...chartOptions, scales: { y: { beginAtZero: true, max: undefined } } }} />
              </div>
            </Card>
            <Card 
              size="small"
              title={<span style={{ fontSize: 14 }}><HddOutlined /> 各Worker磁盘使用率</span>}
            >
              <div style={{ height: 180 }}>
                <Bar data={diskUsageData} options={{ ...chartOptions, scales: { y: { beginAtZero: true, max: 100 } } }} />
              </div>
            </Card>
          </Col>
        </Row>
      </div>

      {/* 查看全部Worker的 Drawer */}
      <Drawer
        title={<><CloudServerOutlined /> 全部Worker 状态</>}
        placement="right"
        width={800}
        onClose={() => setShowAllWorkers(false)}
        open={showAllWorkers}
      >
        <Row gutter={[12, 12]}>
          {workers.map((worker) => (
            <Col key={worker.id} span={8}>
              <Card 
                className={`worker-card-drawer worker-${worker.status}`} 
                hoverable
                onClick={() => {
                  setShowAllWorkers(false)  // 先关闭全部WorkerDrawer
                  setTimeout(() => setSelectedWorker(worker), 100)  // 稍后打开详情Drawer，避免动画冲突
                }}
                size="small"
              >
                <div className="worker-header-drawer">
                  <div style={{ flex: 1 }}>
                    <h4>{worker.name}</h4>
                    <p className="worker-version">
                      {getOsIcon(worker.os)} {getOsName(worker.os)} · {worker.version}
                    </p>
                  </div>
                  <Tag color={getStatusColor(worker.status)} style={{ fontSize: 10 }}>
                    {getStatusText(worker.status)}
                  </Tag>
                </div>
                <div className="worker-metrics-drawer">
                  <div className="metric-item-drawer">
                    <div className="metric-label-drawer">
                      <span>CPU</span>
                      <span>{Math.round(worker.cpu)}%</span>
                    </div>
                  <Progress
                    percent={Math.round(worker.cpu)}
                    strokeColor={worker.cpu > 80 ? '#ff4d4f' : worker.cpu > 60 ? '#faad14' : '#1890ff'}
                    showInfo={false}
                    size="small"
                  />
                  </div>
                  <div className="metric-item-drawer">
                    <div className="metric-label-drawer">
                      <span>内存</span>
                      <span>{Math.round(worker.memory)}%</span>
                    </div>
                  <Progress
                    percent={Math.round(worker.memory)}
                    strokeColor={worker.memory > 80 ? '#ff4d4f' : worker.memory > 60 ? '#faad14' : '#52c41a'}
                    showInfo={false}
                    size="small"
                  />
                  </div>
                  <div className="metric-item-drawer">
                    <div className="metric-label-drawer">
                      <span>任务</span>
                      <span>{worker.tasks}个</span>
                    </div>
                    <div className="task-indicators-compact">
                      {Array.from({ length: Math.min(worker.tasks, 4) }).map((_, i) => (
                        <span key={i} className={`indicator indicator-${i % 3 === 0 ? 'success' : i % 3 === 1 ? 'warning' : 'error'}`} />
                      ))}
                      {worker.tasks > 4 && <span className="more-compact">+{worker.tasks - 4}</span>}
                    </div>
                  </div>
                  <div className="worker-uptime-drawer">
                    <ClockCircleOutlined style={{ fontSize: 10 }} /> {worker.uptime}
                  </div>
                </div>
              </Card>
            </Col>
          ))}
        </Row>
      </Drawer>

      {/* 全部告警 Drawer */}
      <Drawer
        title={<><WarningOutlined /> 全部告警 <Badge count={alerts.length} style={{ marginLeft: 8 }} /></>}
        placement="right"
        width={500}
        onClose={() => setShowAllAlerts(false)}
        open={showAllAlerts}
      >
        <div className="alerts-list">
          {alerts.length === 0 ? (
            <div style={{ 
              display: 'flex', 
              flexDirection: 'column', 
              alignItems: 'center', 
              justifyContent: 'center', 
              height: 200,
              color: token.colorTextSecondary 
            }}>
              <CheckCircleOutlined style={{ fontSize: 40, marginBottom: 12, color: token.colorSuccess }} />
              <span style={{ fontSize: 13 }}>暂无告警，系统运行正常</span>
            </div>
          ) : (
            alerts.map((alert) => (
              <div key={alert.id} className={`alert-item-compact alert-${alert.type}`}>
                <div className="alert-icon-compact">{getAlertIcon(alert.type)}</div>
                <div className="alert-details-compact">
                  <div className="alert-header-compact">
                    <span className="alert-title-compact">{alert.title}</span>
                    <span className="alert-time-compact">{alert.time}</span>
                  </div>
                  <p className="alert-message-compact">{alert.message}</p>
                </div>
              </div>
            ))
          )}
        </div>
      </Drawer>

      {/* Worker 详情 Drawer */}
      <Drawer
        title={<><CloudServerOutlined /> Worker 详情 - {selectedWorker?.name}</>}
        placement="right"
        width={600}
        onClose={() => setSelectedWorker(null)}
        open={!!selectedWorker}
      >
        {selectedWorker && (
          <div>
            <Descriptions column={2} bordered size="small" labelStyle={{ width: 100 }} contentStyle={{ width: 150 }}>
              <Descriptions.Item label="Worker 名称" span={2}>{selectedWorker.name}</Descriptions.Item>
              <Descriptions.Item label="地址">
                <span style={{ fontFamily: 'monospace' }}>{selectedWorker.host}:{selectedWorker.port}</span>
              </Descriptions.Item>
              <Descriptions.Item label="版本">{selectedWorker.version}</Descriptions.Item>
              <Descriptions.Item label="状态">
                <Tag color={getStatusColor(selectedWorker.status)}>{getStatusText(selectedWorker.status)}</Tag>
              </Descriptions.Item>
              <Descriptions.Item label="运行时间">{selectedWorker.uptime}</Descriptions.Item>
              <Descriptions.Item label="CPU使用率">
                <div style={{ width: '100%' }}>
                  <Tooltip
                    title={selectedWorker.cpuCores ? (
                      <div>
                        <div>核心数: {selectedWorker.cpuCores} 核</div>
                      </div>
                    ) : undefined}
                  >
                    <Progress 
                      percent={Math.round(selectedWorker.cpu)} 
                      size="small" 
                      strokeColor={selectedWorker.cpu > 80 ? '#ff4d4f' : selectedWorker.cpu > 60 ? '#faad14' : '#1890ff'}
                    />
                  </Tooltip>
                </div>
              </Descriptions.Item>
              <Descriptions.Item label="内存使用率">
                <div style={{ width: '100%' }}>
                  <Tooltip
                    title={selectedWorker.memoryTotal ? (
                      <div>
                        <div>总内存: {(selectedWorker.memoryTotal / 1024 / 1024 / 1024).toFixed(2)} GB</div>
                        <div>已使用: {((selectedWorker.memoryUsed || 0) / 1024 / 1024 / 1024).toFixed(2)} GB</div>
                        <div>可用: {((selectedWorker.memoryAvailable || 0) / 1024 / 1024 / 1024).toFixed(2)} GB</div>
                      </div>
                    ) : undefined}
                  >
                    <Progress 
                      percent={Math.round(selectedWorker.memory)} 
                      size="small" 
                      strokeColor={selectedWorker.memory > 80 ? '#ff4d4f' : selectedWorker.memory > 60 ? '#faad14' : '#52c41a'}
                    />
                  </Tooltip>
                </div>
              </Descriptions.Item>
              <Descriptions.Item label="磁盘使用率">
                <div style={{ width: '100%' }}>
                  <Tooltip
                    title={selectedWorker.diskTotal ? (
                      <div>
                        <div>总容量: {(selectedWorker.diskTotal / 1024 / 1024 / 1024).toFixed(2)} GB</div>
                        <div>已使用: {((selectedWorker.diskUsed || 0) / 1024 / 1024 / 1024).toFixed(2)} GB</div>
                        <div>可用: {((selectedWorker.diskFree || 0) / 1024 / 1024 / 1024).toFixed(2)} GB</div>
                      </div>
                    ) : undefined}
                  >
                    <Progress 
                      percent={Math.round(selectedWorker.disk)} 
                      size="small" 
                      strokeColor={selectedWorker.disk > 80 ? '#ff4d4f' : selectedWorker.disk > 60 ? '#faad14' : '#722ed1'}
                    />
                  </Tooltip>
                </div>
              </Descriptions.Item>
              <Descriptions.Item label="任务数量">{selectedWorker.tasks}个</Descriptions.Item>
              {selectedWorker.lastHeartbeat && (
                <Descriptions.Item label="最后心跳" span={2}>
                  {new Date(selectedWorker.lastHeartbeat).toLocaleString('zh-CN')}
                </Descriptions.Item>
              )}
            </Descriptions>

            <Card 
              title={
                <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
                  <span>资源使用趋势（30天）</span>
                  <Button 
                    size="small" 
                    type="link"
                    onClick={() => {
                      if (chartRef.current) {
                        chartRef.current.resetZoom()
                      }
                    }}
                    style={{ fontSize: 11 }}
                  >
                    重置缩放
                  </Button>
                </div>
              } 
              style={{ marginTop: 16 }} 
              size="small"
              extra={
                <span style={{ fontSize: 11, color: token.colorTextTertiary }}>
                  💡 滚轮缩放 · 拖拽平移
                </span>
              }
            >
              <div style={{ height: 250 }}>
                {workerDetailChartData && (
                  <Line 
                    ref={chartRef}
                    data={workerDetailChartData} 
                    options={workerDetailChartOptions} 
                  />
                )}
              </div>
            </Card>

            <Card title="运行任务列表" style={{ marginTop: 16 }} size="small">
              <ResponsiveTable
                dataSource={tasks.filter(t => t.worker === selectedWorker.name)}
                rowKey="id"
                columns={[
                  { 
                    title: '任务名称', 
                    dataIndex: 'name', 
                    key: 'name',
                    width: 150,
                    ellipsis: { showTitle: false },
                    render: (name: string) => (
                      <Tooltip title={name} placement="topLeft">
                        <span>{name}</span>
                      </Tooltip>
                    )
                  },
                  { 
                    title: '状态', 
                    dataIndex: 'status', 
                    key: 'status',
                    width: 80,
                    render: (status: string) => <Tag color={getStatusColor(status)}>{getStatusText(status)}</Tag>
                  },
                  { title: 'CPU', dataIndex: 'cpu', key: 'cpu', width: 60, render: (cpu: number | string) => typeof cpu === 'number' ? `${cpu}%` : cpu },
                  { title: '内存', dataIndex: 'memory', key: 'memory', width: 60, render: (memory: number | string) => typeof memory === 'number' ? `${memory}%` : memory },
                ]}
                pagination={false}
                size="small"
              />
            </Card>

            <Card title="Worker 日志" style={{ marginTop: 16 }} size="small">
              <div style={{ maxHeight: 300, overflowY: 'auto' }}>
                {workerLogs.filter(log => log.worker === selectedWorker.name).length > 0 ? (
                  <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
                    {workerLogs
                      .filter(log => log.worker === selectedWorker.name)
                      .map((log) => (
                        <div 
                          key={log.id} 
                          className={`worker-log-item worker-log-${log.type}`}
                        >
                          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', gap: 8 }}>
                            <div style={{ flex: 1 }}>
                              <Tag color={getLogTypeColor(log.type)} style={{ marginBottom: 4 }}>
                                {getLogTypeText(log.type)}
                              </Tag>
                              <div style={{ fontSize: 13, lineHeight: 1.6 }}>{log.message}</div>
                            </div>
                            <span style={{ fontSize: 11, color: token.colorTextTertiary, whiteSpace: 'nowrap' }}>{log.time}</span>
                          </div>
                        </div>
                      ))}
                  </div>
                ) : (
                  <div style={{ textAlign: 'center', padding: '20px', color: token.colorTextTertiary }}>
                    暂无日志记录
                  </div>
                )}
              </div>
            </Card>
          </div>
        )}
      </Drawer>
    </div>
  )
}

export default Monitor
