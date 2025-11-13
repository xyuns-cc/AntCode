import React, { useEffect, useState, useRef, useMemo } from 'react'
import { Card, Row, Col, Progress, Tag, Badge, Button, Statistic, Table, Drawer, Descriptions } from 'antd'
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
import {
  Chart as ChartJS,
  CategoryScale,
  LinearScale,
  PointElement,
  LineElement,
  BarElement,
  Title,
  Tooltip,
  Legend,
  Filler
} from 'chart.js'
import zoomPlugin from 'chartjs-plugin-zoom'
import { Line, Bar } from 'react-chartjs-2'
import './monitor.css'

// 注册 Chart.js 组件
ChartJS.register(
  CategoryScale,
  LinearScale,
  PointElement,
  LineElement,
  BarElement,
  Title,
  Tooltip,
  Legend,
  Filler,
  zoomPlugin
)

// 模拟数据类型定义
interface NodeStatus {
  id: string
  name: string
  version: string
  os: 'windows' | 'ubuntu' | 'debian' | 'centos' | 'redhat' | 'alpine' | 'fedora' | 'macos'
  status: 'running' | 'warning' | 'error' | 'stopped'
  cpu: number
  memory: number
  tasks: number
  uptime: string
}

interface Alert {
  id: string
  type: 'error' | 'warning' | 'info'
  title: string
  message: string
  time: string
  node: string
}

interface Task {
  id: string
  name: string
  node: string
  status: 'running' | 'success' | 'failed' | 'pending'
  cpu: number
  memory: number
  duration: string
}

interface NodeLog {
  id: string
  node: string
  type: 'error' | 'warning' | 'info' | 'success'
  message: string
  time: string
}

const Monitor: React.FC = () => {
  const [loading, setLoading] = useState(false)
  const [currentTime, setCurrentTime] = useState(new Date())
  const [isLargeScreen, setIsLargeScreen] = useState(false)
  const [showAllNodes, setShowAllNodes] = useState(false)
  const [selectedNode, setSelectedNode] = useState<NodeStatus | null>(null)
  const chartRef = React.useRef<any>(null)
  const [chartKey, setChartKey] = useState(0) // 用于强制更新图表数据

  // 模拟节点数据（增加更多节点以展示滚动效果）
  const [nodes] = useState<NodeStatus[]>([
    { id: '1', name: 'node-01', version: 'v1.3.0', os: 'ubuntu', status: 'running', cpu: 68, memory: 75, tasks: 16, uptime: '15天 8小时' },
    { id: '2', name: 'node-02', version: 'v1.3.0', os: 'windows', status: 'running', cpu: 45, memory: 62, tasks: 15, uptime: '15天 8小时' },
    { id: '3', name: 'node-03', version: 'v1.3.0', os: 'debian', status: 'warning', cpu: 89, memory: 82, tasks: 18, uptime: '10天 2小时' },
    { id: '4', name: 'node-04', version: 'v1.3.0', os: 'macos', status: 'running', cpu: 52, memory: 68, tasks: 17, uptime: '20天 5小时' },
    { id: '5', name: 'node-05', version: 'v1.3.0', os: 'centos', status: 'running', cpu: 38, memory: 55, tasks: 14, uptime: '25天 12小时' },
    { id: '6', name: 'node-06', version: 'v1.3.0', os: 'windows', status: 'running', cpu: 55, memory: 60, tasks: 13, uptime: '18天 3小时' },
    { id: '7', name: 'node-07', version: 'v1.3.0', os: 'alpine', status: 'error', cpu: 95, memory: 91, tasks: 20, uptime: '5天 10小时' },
    { id: '8', name: 'node-08', version: 'v1.3.0', os: 'fedora', status: 'running', cpu: 42, memory: 58, tasks: 12, uptime: '30天 7小时' },
  ])

  // 模拟告警数据
  const [alerts] = useState<Alert[]>([
    { id: '1', type: 'error', title: 'CPU使用率过高', message: 'node-03 节点CPU使用率持续15分钟超过85%，当前91%', time: '10分钟前', node: 'node-03' },
    { id: '2', type: 'warning', title: '内存资源不足', message: 'node-03 节点内存使用率82%，建议迁移部分任务', time: '25分钟前', node: 'node-03' },
    { id: '3', type: 'warning', title: '磁盘空间不足', message: '存储节点剩余空间低于20%，当前可用18%', time: '1小时前', node: 'storage-01' },
    { id: '4', type: 'error', title: '任务执行失败', message: 'task "data-process-05" 在node-02上执行失败，错误代码: 500', time: '3小时前', node: 'node-02' },
    { id: '5', type: 'info', title: '节点连接恢复', message: 'node-04 节点网络连接已恢复正常', time: '5小时前', node: 'node-04' },
  ])

  // 模拟任务数据
  const [tasks] = useState<Task[]>([
    { id: '1', name: 'data-sync-daily', node: 'node-01', status: 'running', cpu: 18, memory: 25, duration: '5分12秒' },
    { id: '2', name: 'log-analyzer', node: 'node-02', status: 'running', cpu: 42, memory: 68, duration: '12分35秒' },
    { id: '3', name: 'backup-task', node: 'node-03', status: 'running', cpu: 78, memory: 82, duration: '28分18秒' },
    { id: '4', name: 'report-generator', node: 'node-02', status: 'failed', cpu: 0, memory: 0, duration: '-' },
    { id: '5', name: 'data-cleanup', node: 'node-04', status: 'success', cpu: 32, memory: 45, duration: '8分45秒' },
    { id: '6', name: 'alert-monitor', node: 'node-01', status: 'running', cpu: 28, memory: 35, duration: '2分58秒' },
  ])

  // 模拟节点日志数据
  const [nodeLogs] = useState<NodeLog[]>([
    { id: '1', node: 'node-01', type: 'info', message: '任务 data-sync-daily 启动成功', time: '2分钟前' },
    { id: '2', node: 'node-01', type: 'success', message: '系统健康检查通过', time: '5分钟前' },
    { id: '3', node: 'node-02', type: 'error', message: '任务 report-generator 执行失败: 连接超时', time: '3分钟前' },
    { id: '4', node: 'node-02', type: 'warning', message: '内存使用率超过60%', time: '10分钟前' },
    { id: '5', node: 'node-03', type: 'error', message: 'CPU使用率持续超过85%', time: '5分钟前' },
    { id: '6', node: 'node-03', type: 'warning', message: '磁盘空间不足，剩余18%', time: '15分钟前' },
    { id: '7', node: 'node-03', type: 'info', message: '备份任务正在执行中', time: '20分钟前' },
    { id: '8', node: 'node-04', type: 'success', message: '数据清理任务完成', time: '8分钟前' },
    { id: '9', node: 'node-04', type: 'info', message: '网络连接已恢复', time: '30分钟前' },
    { id: '10', node: 'node-05', type: 'info', message: '定时任务调度器启动', time: '1小时前' },
    { id: '11', node: 'node-07', type: 'error', message: '系统资源严重不足', time: '2分钟前' },
    { id: '12', node: 'node-07', type: 'error', message: '多个任务执行失败', time: '5分钟前' },
  ])

  // 生成过去24小时的时间标签
  const generateTimeLabels = () => {
    const labels = []
    const now = new Date()
    for (let i = 23; i >= 0; i--) {
      const hour = new Date(now.getTime() - i * 60 * 60 * 1000)
      labels.push(`${hour.getHours()}:00`)
    }
    return labels
  }

  // 生成过去30天的时间标签（用于节点详情）
  // 生成过去30天的日期标签（使用 useMemo 避免重新生成）
  const dayLabels = useMemo(() => {
    const labels = []
    const now = new Date()
    for (let i = 29; i >= 0; i--) {
      const day = new Date(now.getTime() - i * 24 * 60 * 60 * 1000)
      labels.push(`${day.getMonth() + 1}/${day.getDate()}`)
    }
    return labels
  }, [])

  // 生成随机数据
  const generateRandomData = (count: number, min: number, max: number) => {
    return Array.from({ length: count }, () => Math.floor(Math.random() * (max - min + 1)) + min)
  }

  const timeLabels = generateTimeLabels()

  // 添加定时器更新图表数据
  useEffect(() => {
    if (!selectedNode || !chartRef.current) return
    
    const interval = setInterval(() => {
      const chart = chartRef.current
      if (chart) {
        // 更新数据但保持缩放状态
        chart.data.datasets[0].data = generateRandomData(30, Math.max(0, selectedNode.cpu - 15), Math.min(100, selectedNode.cpu + 15))
        chart.data.datasets[1].data = generateRandomData(30, Math.max(0, selectedNode.memory - 15), Math.min(100, selectedNode.memory + 15))
        chart.update('none') // 'none' 模式不会重置缩放
      }
    }, 3000) // 每3秒更新一次
    
    return () => clearInterval(interval)
  }, [selectedNode])

  // 生成节点详情图表数据
  const getNodeDetailChartData = () => {
    if (!selectedNode) return null
    return {
      labels: dayLabels,
      datasets: [
        {
          label: 'CPU',
          data: generateRandomData(30, Math.max(0, selectedNode.cpu - 15), Math.min(100, selectedNode.cpu + 15)),
          borderColor: '#1890ff',
          backgroundColor: (context: any) => {
            const ctx = context.chart.ctx
            const gradient = ctx.createLinearGradient(0, 0, 0, 250)
            gradient.addColorStop(0, 'rgba(24, 144, 255, 0.3)')
            gradient.addColorStop(1, 'rgba(24, 144, 255, 0.01)')
            return gradient
          },
          tension: 0.4,
          fill: true,
          borderWidth: 2.5,
          pointRadius: 2,
          pointHoverRadius: 6,
          pointBackgroundColor: '#1890ff',
          pointHoverBackgroundColor: '#1890ff',
          pointHoverBorderColor: '#fff',
          pointHoverBorderWidth: 2,
        },
        {
          label: '内存',
          data: generateRandomData(30, Math.max(0, selectedNode.memory - 15), Math.min(100, selectedNode.memory + 15)),
          borderColor: '#52c41a',
          backgroundColor: (context: any) => {
            const ctx = context.chart.ctx
            const gradient = ctx.createLinearGradient(0, 0, 0, 250)
            gradient.addColorStop(0, 'rgba(82, 196, 26, 0.3)')
            gradient.addColorStop(1, 'rgba(82, 196, 26, 0.01)')
            return gradient
          },
          tension: 0.4,
          fill: true,
          borderWidth: 2.5,
          pointRadius: 2,
          pointHoverRadius: 6,
          pointBackgroundColor: '#52c41a',
          pointHoverBackgroundColor: '#52c41a',
          pointHoverBorderColor: '#fff',
          pointHoverBorderWidth: 2,
        },
      ],
    }
  }

  // CPU趋势数据 - 显示集群平均值、最大值和最小值
  const cpuTrendData = {
    labels: timeLabels,
    datasets: [
      {
        label: '平均',
        data: generateRandomData(24, 50, 65),
        borderColor: '#1890ff',
        backgroundColor: (context: any) => {
          const ctx = context.chart.ctx
          const gradient = ctx.createLinearGradient(0, 0, 0, 200)
          gradient.addColorStop(0, 'rgba(24, 144, 255, 0.3)')
          gradient.addColorStop(1, 'rgba(24, 144, 255, 0.01)')
          return gradient
        },
        tension: 0.4,
        fill: true,
        borderWidth: 3,
        pointRadius: 0,
        pointHoverRadius: 6,
        pointHoverBackgroundColor: '#1890ff',
        pointHoverBorderColor: '#fff',
        pointHoverBorderWidth: 2,
      },
      {
        label: '最大',
        data: generateRandomData(24, 70, 90),
        borderColor: '#ff7875',
        backgroundColor: 'transparent',
        tension: 0.4,
        fill: false,
        borderWidth: 2,
        borderDash: [8, 4],
        pointRadius: 0,
        pointHoverRadius: 5,
        pointHoverBackgroundColor: '#ff4d4f',
        pointHoverBorderColor: '#fff',
        pointHoverBorderWidth: 2,
      },
      {
        label: '最小',
        data: generateRandomData(24, 30, 45),
        borderColor: '#95de64',
        backgroundColor: 'transparent',
        tension: 0.4,
        fill: false,
        borderWidth: 2,
        borderDash: [8, 4],
        pointRadius: 0,
        pointHoverRadius: 5,
        pointHoverBackgroundColor: '#52c41a',
        pointHoverBorderColor: '#fff',
        pointHoverBorderWidth: 2,
      },
    ],
  }

  // 内存趋势数据 - 显示集群平均值、最大值和最小值
  const memoryTrendData = {
    labels: timeLabels,
    datasets: [
      {
        label: '平均',
        data: generateRandomData(24, 55, 70),
        borderColor: '#722ed1',
        backgroundColor: (context: any) => {
          const ctx = context.chart.ctx
          const gradient = ctx.createLinearGradient(0, 0, 0, 200)
          gradient.addColorStop(0, 'rgba(114, 46, 209, 0.3)')
          gradient.addColorStop(1, 'rgba(114, 46, 209, 0.01)')
          return gradient
        },
        tension: 0.4,
        fill: true,
        borderWidth: 3,
        pointRadius: 0,
        pointHoverRadius: 6,
        pointHoverBackgroundColor: '#722ed1',
        pointHoverBorderColor: '#fff',
        pointHoverBorderWidth: 2,
      },
      {
        label: '最大',
        data: generateRandomData(24, 75, 90),
        borderColor: '#ff7875',
        backgroundColor: 'transparent',
        tension: 0.4,
        fill: false,
        borderWidth: 2,
        borderDash: [8, 4],
        pointRadius: 0,
        pointHoverRadius: 5,
        pointHoverBackgroundColor: '#ff4d4f',
        pointHoverBorderColor: '#fff',
        pointHoverBorderWidth: 2,
      },
      {
        label: '最小',
        data: generateRandomData(24, 40, 55),
        borderColor: '#95de64',
        backgroundColor: 'transparent',
        tension: 0.4,
        fill: false,
        borderWidth: 2,
        borderDash: [8, 4],
        pointRadius: 0,
        pointHoverRadius: 5,
        pointHoverBackgroundColor: '#52c41a',
        pointHoverBorderColor: '#fff',
        pointHoverBorderWidth: 2,
      },
    ],
  }

  // 任务执行统计数据
  const taskStatsData = {
    labels: ['成功', '失败', '运行中', '待执行'],
    datasets: [
      {
        label: '任务数量',
        data: [156, 12, 8, 24],
        backgroundColor: ['#52c41a', '#ff4d4f', '#1890ff', '#faad14'],
      },
    ],
  }

  // 网络流量数据
  const networkData = {
    labels: timeLabels.slice(-12),
    datasets: [
      {
        label: '接收',
        data: generateRandomData(12, 300, 800),
        borderColor: '#52c41a',
        backgroundColor: 'rgba(82, 196, 26, 0.1)',
        tension: 0.4,
        fill: true,
      },
      {
        label: '发送',
        data: generateRandomData(12, 200, 600),
        borderColor: '#1890ff',
        backgroundColor: 'rgba(24, 144, 255, 0.1)',
        tension: 0.4,
        fill: true,
      },
    ],
  }

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
          color: '#666',
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
          label: function(context: any) {
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
          callback: function(value: any) {
            return value + '%'
          },
          font: {
            size: 11
          },
          color: '#999',
        },
        grid: {
          color: 'rgba(0, 0, 0, 0.06)',
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
          color: '#999',
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

  // 节点详情图表配置 - 支持缩放和滚动（使用 useMemo 避免重新创建）
  const nodeDetailChartOptions = useMemo(() => ({
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
          color: '#666',
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
          label: function(context: any) {
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
        zoom: {
          wheel: {
            enabled: true,
            speed: 0.1,
          },
          pinch: {
            enabled: true,
          },
          mode: 'x' as const,
        },
        pan: {
          enabled: true,
          mode: 'x' as const,
        },
        limits: {
          x: { min: 'original', max: 'original' },
        },
      },
    },
    scales: {
      y: {
        beginAtZero: true,
        max: 100,
        ticks: {
          callback: function(value: any) {
            return value + '%'
          },
          font: {
            size: 11
          },
          color: '#999',
        },
        grid: {
          color: 'rgba(0, 0, 0, 0.06)',
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
          color: '#999',
        },
        grid: {
          display: false,
        },
        border: {
          display: false,
        },
      },
    },
  }), [])

  // 更新时间
  useEffect(() => {
    const timer = setInterval(() => {
      setCurrentTime(new Date())
    }, 1000)
    return () => clearInterval(timer)
  }, [])

  // 刷新数据
  const handleRefresh = () => {
    setLoading(true)
    setTimeout(() => {
      setLoading(false)
    }, 1000)
  }

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
        return <WindowsOutlined style={{ fontSize: 14, marginRight: 4, color: '#00a4ef' }} />
      case 'ubuntu':
      case 'debian':
      case 'centos':
      case 'redhat':
      case 'alpine':
      case 'fedora':
        return <LinuxOutlined style={{ fontSize: 14, marginRight: 4, color: '#fcc624' }} />
      case 'macos':
        return <AppleOutlined style={{ fontSize: 14, marginRight: 4, color: '#555' }} />
      default:
        return <LinuxOutlined style={{ fontSize: 14, marginRight: 4, color: '#fcc624' }} />
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
      default: return '未知'
    }
  }

  // 获取告警图标
  const getAlertIcon = (type: string) => {
    switch (type) {
      case 'error': return <CloseCircleOutlined style={{ color: '#ff4d4f' }} />
      case 'warning': return <WarningOutlined style={{ color: '#faad14' }} />
      case 'info': return <CheckCircleOutlined style={{ color: '#1890ff' }} />
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
            <CloudServerOutlined style={{ marginRight: 8, color: '#722ed1' }} />
            节点监控
          </h2>
          <div className="header-badges">
            <Badge status="success" text="系统正常" />
            <span style={{ fontSize: 12, color: '#888', marginLeft: 16 }}>
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
            <div className="stat-icon">
              <CloudServerOutlined style={{ color: '#722ed1' }} />
            </div>
            <div className="stat-content">
              <div className="stat-value">8</div>
              <div className="stat-label">执行节点</div>
            </div>
          </div>
        </Col>
        <Col xs={12} sm={6}>
          <div className="mini-stat-card">
            <div className="stat-icon">
              <ThunderboltOutlined style={{ color: '#1890ff' }} />
            </div>
            <div className="stat-content">
              <div className="stat-value">124</div>
              <div className="stat-label">运行任务</div>
            </div>
          </div>
        </Col>
        <Col xs={12} sm={6}>
          <div className="mini-stat-card">
            <div className="stat-icon">
              <WarningOutlined style={{ color: '#faad14' }} />
            </div>
            <div className="stat-content">
              <div className="stat-value">3</div>
              <div className="stat-label">警告</div>
            </div>
          </div>
        </Col>
        <Col xs={12} sm={6}>
          <div className="mini-stat-card">
            <div className="stat-icon">
              <CloseCircleOutlined style={{ color: '#ff4d4f' }} />
            </div>
            <div className="stat-content">
              <div className="stat-value">1</div>
              <div className="stat-label">错误</div>
            </div>
          </div>
        </Col>
      </Row>

      {/* 主要内容区域 */}
      <div style={{ marginTop: 0 }}>
        {/* 执行节点状态 - 水平滚动 */}
        <Card
          size="small"
          title={
            <span style={{ fontSize: 14 }}>
              <CloudServerOutlined /> 执行节点状态 ({nodes.length}个)
            </span>
          }
          extra={
            <div style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
              <span style={{ fontSize: 11, color: '#888' }}>上次检查: 2分钟前</span>
              <Button 
                size="small" 
                type="link" 
                onClick={() => setShowAllNodes(true)}
                style={{ fontSize: 12 }}
              >
                查看全部 <RightOutlined />
              </Button>
            </div>
          }
          style={{ marginBottom: 12 }}
        >
            <div className="nodes-scroll-container">
              {nodes.map((node) => (
                <Card 
                  key={node.id} 
                  className={`node-card-compact node-${node.status}`} 
                  hoverable
                  onClick={() => setSelectedNode(node)}
                >
                  <div className="node-header-compact">
                    <div>
                      <h4>{node.name}</h4>
                      <p className="node-version">
                        {getOsIcon(node.os)} {getOsName(node.os)} · {node.version}
                      </p>
                    </div>
                    <Tag color={getStatusColor(node.status)} style={{ fontSize: 10 }}>
                      {getStatusText(node.status)}
                    </Tag>
                  </div>
                  <div className="node-metrics-compact">
                    <div className="metric-row">
                      <span className="metric-label-compact">CPU</span>
                      <div className="metric-value-compact">
                        <Progress
                          percent={node.cpu}
                          strokeColor={node.cpu > 80 ? '#ff4d4f' : node.cpu > 60 ? '#faad14' : '#1890ff'}
                          showInfo={false}
                          size="small"
                          strokeWidth={6}
                          style={{ width: '100%' }}
                        />
                        <span className="metric-percent">{node.cpu}%</span>
                      </div>
                    </div>
                    <div className="metric-row">
                      <span className="metric-label-compact">内存</span>
                      <div className="metric-value-compact">
                        <Progress
                          percent={node.memory}
                          strokeColor={node.memory > 80 ? '#ff4d4f' : node.memory > 60 ? '#faad14' : '#52c41a'}
                          showInfo={false}
                          size="small"
                          strokeWidth={6}
                          style={{ width: '100%' }}
                        />
                        <span className="metric-percent">{node.memory}%</span>
                      </div>
                    </div>
                    <div className="metric-item-compact">
                      <div className="metric-label-compact">
                        <span>任务</span>
                        <span>{node.tasks}个</span>
                      </div>
                      <div className="task-indicators-mini">
                        {Array.from({ length: Math.min(node.tasks, 4) }).map((_, i) => (
                          <span key={i} className={`indicator-mini indicator-${i % 3 === 0 ? 'success' : i % 3 === 1 ? 'warning' : 'error'}`} />
                        ))}
                        {node.tasks > 4 && <span className="more-mini">+{node.tasks - 4}</span>}
                      </div>
                    </div>
                  </div>
                </Card>
              ))}
            </div>
        </Card>

        {/* 第二行：资源告警和性能趋势 */}
        <Row gutter={12} style={{ marginBottom: 12 }}>
          {/* 资源告警 */}
          <Col xs={24} lg={12}>
            <Card
              size="small"
              title={<span style={{ fontSize: 14 }}><WarningOutlined /> 资源告警</span>}
              extra={<a style={{ fontSize: 11 }}>查看全部</a>}
              className="alerts-card"
            >
            <div className="alerts-list">
              {alerts.map((alert) => (
                <Card key={alert.id} className={`alert-item alert-${alert.type}`} size="small">
                  <div className="alert-content">
                    <div className="alert-icon">{getAlertIcon(alert.type)}</div>
                    <div className="alert-details">
                      <div className="alert-header">
                        <h4>{alert.title}</h4>
                        <span className="alert-time">{alert.time}</span>
                      </div>
                      <p className="alert-message">{alert.message}</p>
                      <div className="alert-actions">
                        <Button size="small" type="link">处理</Button>
                      </div>
                    </div>
                  </div>
                </Card>
              ))}
            </div>
          </Card>
        </Col>

          {/* 性能趋势 */}
          <Col xs={24} lg={12}>
            <Card
              size="small"
              title={<span style={{ fontSize: 14 }}><ThunderboltOutlined /> 性能趋势</span>}
              extra={
                <Button.Group size="small">
                  <Button type="primary" size="small">24h</Button>
                  <Button size="small">7d</Button>
                  <Button size="small">30d</Button>
                </Button.Group>
              }
            >
            <div style={{ marginBottom: 16 }}>
              <p style={{ fontSize: 12, marginBottom: 10, color: '#888', fontWeight: 500 }}>
                集群CPU使用率
              </p>
              <div style={{ 
                height: 180, 
                padding: '12px',
                borderRadius: '6px',
                background: 'rgba(24, 144, 255, 0.02)'
              }}>
                <Line data={cpuTrendData} options={chartOptions} />
              </div>
            </div>
            <div>
              <p style={{ fontSize: 12, marginBottom: 10, color: '#888', fontWeight: 500 }}>
                集群内存使用率
              </p>
              <div style={{ 
                height: 180, 
                padding: '12px',
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
            <Table
              dataSource={tasks}
              columns={[
                {
                  title: '任务名称',
                  dataIndex: 'name',
                  key: 'name',
                },
                {
                  title: '执行节点',
                  dataIndex: 'node',
                  key: 'node',
                },
                {
                  title: '状态',
                  dataIndex: 'status',
                  key: 'status',
                  render: (status) => <Tag color={getStatusColor(status)}>{getStatusText(status)}</Tag>,
                },
                {
                  title: 'CPU',
                  dataIndex: 'cpu',
                  key: 'cpu',
                  render: (cpu) => `${cpu}%`,
                },
                {
                  title: '内存',
                  dataIndex: 'memory',
                  key: 'memory',
                  render: (memory) => `${memory}%`,
                },
                {
                  title: '运行时长',
                  dataIndex: 'duration',
                  key: 'duration',
                },
                {
                  title: '操作',
                  key: 'action',
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
              title={<span style={{ fontSize: 14 }}><HddOutlined /> 网络流量 (MB/s)</span>}
            >
              <div style={{ height: 180 }}>
                <Line data={networkData} options={{ ...chartOptions, scales: { y: { beginAtZero: true, max: undefined } } }} />
              </div>
            </Card>
          </Col>
        </Row>
      </div>

      {/* 查看全部节点的 Drawer */}
      <Drawer
        title={<><CloudServerOutlined /> 全部节点状态</>}
        placement="right"
        width={800}
        onClose={() => setShowAllNodes(false)}
        open={showAllNodes}
      >
        <Row gutter={[12, 12]}>
          {nodes.map((node) => (
            <Col key={node.id} span={8}>
              <Card 
                className={`node-card-drawer node-${node.status}`} 
                hoverable
                onClick={() => setSelectedNode(node)}
                size="small"
              >
                <div className="node-header-drawer">
                  <div style={{ flex: 1 }}>
                    <h4>{node.name}</h4>
                    <p className="node-version">
                      {getOsIcon(node.os)} {getOsName(node.os)} · {node.version}
                    </p>
                  </div>
                  <Tag color={getStatusColor(node.status)} style={{ fontSize: 10 }}>
                    {getStatusText(node.status)}
                  </Tag>
                </div>
                <div className="node-metrics-drawer">
                  <div className="metric-item-drawer">
                    <div className="metric-label-drawer">
                      <span>CPU</span>
                      <span>{node.cpu}%</span>
                    </div>
                    <Progress
                      percent={node.cpu}
                      strokeColor={node.cpu > 80 ? '#ff4d4f' : node.cpu > 60 ? '#faad14' : '#1890ff'}
                      showInfo={false}
                      size="small"
                      strokeWidth={4}
                    />
                  </div>
                  <div className="metric-item-drawer">
                    <div className="metric-label-drawer">
                      <span>内存</span>
                      <span>{node.memory}%</span>
                    </div>
                    <Progress
                      percent={node.memory}
                      strokeColor={node.memory > 80 ? '#ff4d4f' : node.memory > 60 ? '#faad14' : '#52c41a'}
                      showInfo={false}
                      size="small"
                      strokeWidth={4}
                    />
                  </div>
                  <div className="metric-item-drawer">
                    <div className="metric-label-drawer">
                      <span>任务</span>
                      <span>{node.tasks}个</span>
                    </div>
                    <div className="task-indicators-compact">
                      {Array.from({ length: Math.min(node.tasks, 4) }).map((_, i) => (
                        <span key={i} className={`indicator indicator-${i % 3 === 0 ? 'success' : i % 3 === 1 ? 'warning' : 'error'}`} />
                      ))}
                      {node.tasks > 4 && <span className="more-compact">+{node.tasks - 4}</span>}
                    </div>
                  </div>
                  <div className="node-uptime-drawer">
                    <ClockCircleOutlined style={{ fontSize: 10 }} /> {node.uptime}
                  </div>
                </div>
              </Card>
            </Col>
          ))}
        </Row>
      </Drawer>

      {/* 节点详情 Drawer */}
      <Drawer
        title={<><CloudServerOutlined /> 节点详情 - {selectedNode?.name}</>}
        placement="right"
        width={600}
        onClose={() => setSelectedNode(null)}
        open={!!selectedNode}
      >
        {selectedNode && (
          <div>
            <Descriptions column={2} bordered size="small">
              <Descriptions.Item label="节点名称" span={2}>{selectedNode.name}</Descriptions.Item>
              <Descriptions.Item label="操作系统">
                <span style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
                  {getOsIcon(selectedNode.os)} {getOsName(selectedNode.os)}
                </span>
              </Descriptions.Item>
              <Descriptions.Item label="版本">{selectedNode.version}</Descriptions.Item>
              <Descriptions.Item label="状态">
                <Tag color={getStatusColor(selectedNode.status)}>{getStatusText(selectedNode.status)}</Tag>
              </Descriptions.Item>
              <Descriptions.Item label="运行时间">{selectedNode.uptime}</Descriptions.Item>
              <Descriptions.Item label="CPU使用率">
                <Progress percent={selectedNode.cpu} size="small" />
              </Descriptions.Item>
              <Descriptions.Item label="内存使用率">
                <Progress percent={selectedNode.memory} size="small" />
              </Descriptions.Item>
              <Descriptions.Item label="任务数量" span={2}>{selectedNode.tasks}个</Descriptions.Item>
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
                <span style={{ fontSize: 11, color: '#999' }}>
                  💡 滚轮缩放 · 拖拽平移
                </span>
              }
            >
              <div style={{ height: 250 }}>
                {selectedNode && (
                  <Line 
                    ref={chartRef}
                    data={getNodeDetailChartData()!} 
                    options={nodeDetailChartOptions} 
                  />
                )}
              </div>
            </Card>

            <Card title="运行任务列表" style={{ marginTop: 16 }} size="small">
              <Table
                dataSource={tasks.filter(t => t.node === selectedNode.name)}
                columns={[
                  { title: '任务名称', dataIndex: 'name', key: 'name' },
                  { 
                    title: '状态', 
                    dataIndex: 'status', 
                    key: 'status',
                    render: (status) => <Tag color={getStatusColor(status)}>{getStatusText(status)}</Tag>
                  },
                  { title: 'CPU', dataIndex: 'cpu', key: 'cpu', render: (cpu) => `${cpu}%` },
                  { title: '内存', dataIndex: 'memory', key: 'memory', render: (memory) => `${memory}%` },
                ]}
                pagination={false}
                size="small"
              />
            </Card>

            <Card title="节点日志" style={{ marginTop: 16 }} size="small">
              <div style={{ maxHeight: 300, overflowY: 'auto' }}>
                {nodeLogs.filter(log => log.node === selectedNode.name).length > 0 ? (
                  <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
                    {nodeLogs
                      .filter(log => log.node === selectedNode.name)
                      .map((log) => (
                        <div 
                          key={log.id} 
                          className={`node-log-item node-log-${log.type}`}
                        >
                          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', gap: 8 }}>
                            <div style={{ flex: 1 }}>
                              <Tag color={getLogTypeColor(log.type)} style={{ marginBottom: 4 }}>
                                {getLogTypeText(log.type)}
                              </Tag>
                              <div style={{ fontSize: 13, lineHeight: 1.6 }}>{log.message}</div>
                            </div>
                            <span style={{ fontSize: 11, color: '#999', whiteSpace: 'nowrap' }}>{log.time}</span>
                          </div>
                        </div>
                      ))}
                  </div>
                ) : (
                  <div style={{ textAlign: 'center', padding: '20px', color: '#999' }}>
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

