import type { ChartData } from 'chart.js'
import { formatTimeLabel } from '../data'
import type {
  ClusterHistory,
  MonitorTaskCounts,
  PerformancePeriod,
  WorkerDisplayData,
  WorkerHistoryPoint,
} from '../types'

interface ClusterMetrics {
  avgCpu: number
  avgMem: number
  maxCpu: number
  maxMem: number
  minCpu: number
  minMem: number
}

export const calculateClusterMetrics = (workers: WorkerDisplayData[]): ClusterMetrics => {
  if (workers.length === 0) {
    return { avgCpu: 0, avgMem: 0, maxCpu: 0, maxMem: 0, minCpu: 0, minMem: 0 }
  }
  const cpu = workers.map((worker) => worker.cpu)
  const memory = workers.map((worker) => worker.memory)
  return {
    avgCpu: Math.round(cpu.reduce((sum, value) => sum + value, 0) / workers.length),
    avgMem: Math.round(memory.reduce((sum, value) => sum + value, 0) / workers.length),
    maxCpu: Math.max(...cpu),
    maxMem: Math.max(...memory),
    minCpu: Math.min(...cpu),
    minMem: Math.min(...memory),
  }
}

export const createTrendData = (
  history: ClusterHistory | null,
  metric: 'cpu' | 'memory',
  period: PerformancePeriod,
  current: ClusterMetrics
): ChartData<'line'> => {
  if (!history || history.timestamps.length === 0) {
    return createCurrentTrendData(metric, current)
  }
  const color = metric === 'cpu' ? '#1890ff' : '#722ed1'
  const background = metric === 'cpu' ? 'rgba(24, 144, 255, 0.1)' : 'rgba(114, 46, 209, 0.1)'
  return {
    labels: history.timestamps.map((timestamp) => formatTimeLabel(timestamp, period)),
    datasets: [
      createTrendDataset({ label: '平均', data: history[metric].avg, borderColor: color, backgroundColor: background, dashed: false }),
      createTrendDataset({ label: '最大', data: history[metric].max, borderColor: '#ff7875', backgroundColor: 'transparent', dashed: true }),
      createTrendDataset({ label: '最小', data: history[metric].min, borderColor: '#95de64', backgroundColor: 'transparent', dashed: true }),
    ],
  }
}

interface TrendDatasetSpec {
  label: string
  data: number[]
  borderColor: string
  backgroundColor: string
  dashed: boolean
}

// 单点折线没有线段可画，pointRadius: 0 会让整张图彻底空白（有轴、有图例、
// 没有任何数据痕迹），看起来和"接口没返回数据"一模一样。30d 按天聚合，
// 集群跑了不到两天时后端就只给一个点 —— 那是有数据，必须画出来。
const SINGLE_POINT_RADIUS = 5

const createTrendDataset = (spec: TrendDatasetSpec) => ({
  label: spec.label,
  data: spec.data,
  borderColor: spec.borderColor,
  backgroundColor: spec.backgroundColor,
  tension: 0.4,
  fill: !spec.dashed,
  borderWidth: spec.dashed ? 2 : 3,
  borderDash: spec.dashed ? [8, 4] : undefined,
  pointRadius: spec.data.length === 1 ? SINGLE_POINT_RADIUS : 0,
  pointHoverRadius: spec.dashed ? 5 : 6,
})

const createCurrentTrendData = (
  metric: 'cpu' | 'memory',
  current: ClusterMetrics
): ChartData<'line'> => {
  const values =
    metric === 'cpu'
      ? [current.avgCpu, current.maxCpu, current.minCpu]
      : [current.avgMem, current.maxMem, current.minMem]
  const primary = metric === 'cpu' ? '#1890ff' : '#722ed1'
  return {
    labels: ['当前'],
    datasets: ['平均', '最大', '最小'].map((label, index) => ({
      label,
      data: [values[index]],
      borderColor: [primary, '#ff7875', '#95de64'][index],
      backgroundColor:
        index === 0
          ? metric === 'cpu'
            ? 'rgba(24, 144, 255, 0.1)'
            : 'rgba(114, 46, 209, 0.1)'
          : undefined,
      borderWidth: index === 0 ? 3 : 2,
      pointRadius: 5,
    })),
  }
}

export const createWorkerDetailData = (
  worker: WorkerDisplayData | null,
  history: WorkerHistoryPoint[]
): ChartData<'line'> | null => {
  if (!worker) return null
  const hasHistory = history.length > 0
  const labels = hasHistory
    ? history.map((point) => {
        const date = new Date(point.timestamp)
        return `${date.getMonth() + 1}/${date.getDate()} ${date.getHours()}:00`
      })
    : ['当前']
  return {
    labels,
    datasets: [
      createWorkerDataset(
        'CPU',
        hasHistory ? history.map((point) => point.cpu) : [worker.cpu],
        '#1890ff',
        hasHistory
      ),
      createWorkerDataset(
        '内存',
        hasHistory ? history.map((point) => point.memory) : [worker.memory],
        '#52c41a',
        hasHistory
      ),
    ],
  }
}

const createWorkerDataset = (
  label: string,
  data: number[],
  color: string,
  hasHistory: boolean
) => ({
  label,
  data,
  borderColor: color,
  backgroundColor: color === '#1890ff' ? 'rgba(24, 144, 255, 0.1)' : 'rgba(82, 196, 26, 0.1)',
  tension: 0.4,
  fill: true,
  borderWidth: 2.5,
  pointRadius: hasHistory ? 1 : 5,
  pointHoverRadius: hasHistory ? 6 : undefined,
})

export const createTaskStatsData = (counts: MonitorTaskCounts): ChartData<'bar'> => {
  return {
    labels: ['成功', '失败', '运行中', '待执行'],
    datasets: [
      {
        label: '任务数量',
        data: [counts.success, counts.failed, counts.running, counts.pending],
        backgroundColor: ['#52c41a', '#ff4d4f', '#1890ff', '#faad14'],
      },
    ],
  }
}

export const createDiskUsageData = (workers: WorkerDisplayData[]): ChartData<'bar'> => {
  if (workers.length === 0) {
    return {
      labels: ['暂无数据'],
      datasets: [{ label: '磁盘使用率', data: [0], backgroundColor: ['#d9d9d9'] }],
    }
  }
  return {
    labels: workers.map((worker) => worker.name),
    datasets: [
      {
        label: '磁盘使用率 (%)',
        data: workers.map((worker) => worker.disk),
        backgroundColor: workers.map((worker) =>
          worker.disk > 80 ? '#ff4d4f' : worker.disk > 60 ? '#faad14' : '#722ed1'
        ),
      },
    ],
  }
}
