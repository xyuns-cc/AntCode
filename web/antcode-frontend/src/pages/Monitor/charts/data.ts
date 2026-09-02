import type { ChartData } from 'chart.js'
import { averageReportedUsage } from '@/utils/metricAverage'
import { formatTimeLabel } from '../data'
import type {
  ClusterHistory,
  MonitorTaskCounts,
  PerformancePeriod,
  WorkerDisplayData,
  WorkerHistoryPoint,
} from '../types'

interface ClusterMetrics {
  avgCpu: number | null
  avgMem: number | null
  maxCpu: number | null
  maxMem: number | null
  minCpu: number | null
  minMem: number | null
}

const NO_CLUSTER_METRICS: ClusterMetrics = {
  avgCpu: null, avgMem: null, maxCpu: null, maxMem: null, minCpu: null, minMem: null,
}

// 没上报过指标的机器既不进均值分母、也不参与极值：按 0 计入会稀释均值，还会把
// 「集群最小 CPU」永久钉在 0。分母口径与后端 worker_stats_service 一致（见 metricAverage）。
export const calculateClusterMetrics = (workers: WorkerDisplayData[]): ClusterMetrics => {
  const reported = workers.filter((worker) => worker.hasMetrics)
  if (reported.length === 0) return NO_CLUSTER_METRICS
  const cpu = reported.map((worker) => worker.cpu)
  const memory = reported.map((worker) => worker.memory)
  return {
    avgCpu: averageReportedUsage(cpu),
    avgMem: averageReportedUsage(memory),
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

// null = 没有任何可画的读数。没有历史点时这张图退化成一个「当前」点，取的是
// transformWorker 压出来的 cpu/memory —— 对一台从没上报过的机器那是两个凭空的 0%
// （见 ../types.ts::hasMetrics），和「这台机器很闲」无法区分。
export const createWorkerDetailData = (
  worker: WorkerDisplayData | null,
  history: WorkerHistoryPoint[]
): ChartData<'line'> | null => {
  if (!worker) return null
  const hasHistory = history.length > 0
  if (!hasHistory && !worker.hasMetrics) return null
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

// 「没有可画的数据」的占位：一根灰色零高柱，标签写明原因。带业务配色的零高柱会被读成
// 「任务数真的都是 0」「磁盘真的是空的」，那是把故障画成正常。
const placeholderBar = (label: string, series: string): ChartData<'bar'> => ({
  labels: [label],
  datasets: [{ label: series, data: [0], backgroundColor: ['#d9d9d9'] }],
})

// null = 汇总这一路没取到（见 hooks/useTasks）。四根 0 高的彩色柱子和「一个任务都没有」
// 长得一模一样。
export const createTaskStatsData = (counts: MonitorTaskCounts | null): ChartData<'bar'> => {
  if (counts === null) return placeholderBar('任务统计未取到', '任务数量')
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

// 没上报过指标的机器 disk 被 transformWorker 压成 0（见 types.ts::hasMetrics）。照单全画
// 就是给一台从没上报过的机器挂一根写着它名字的「0% 磁盘」条——和真的空盘无法区分。
// 与 calculateClusterMetrics 同一道过滤。
export const createDiskUsageData = (workers: WorkerDisplayData[]): ChartData<'bar'> => {
  const reported = workers.filter((worker) => worker.hasMetrics)
  if (reported.length === 0) {
    return placeholderBar(workers.length === 0 ? '暂无数据' : '无机器上报磁盘用量', '磁盘使用率')
  }
  return {
    labels: reported.map((worker) => worker.name),
    datasets: [
      {
        label: '磁盘使用率 (%)',
        data: reported.map((worker) => worker.disk),
        backgroundColor: reported.map((worker) =>
          worker.disk > 80 ? '#ff4d4f' : worker.disk > 60 ? '#faad14' : '#722ed1'
        ),
      },
    ],
  }
}
