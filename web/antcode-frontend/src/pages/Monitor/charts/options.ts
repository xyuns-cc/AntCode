import type { ChartOptions } from 'chart.js'
import type { GlobalToken } from 'antd/es/theme/interface'

const tooltip = {
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
    label(context: { dataset: { label?: string }; parsed: { y: number | null } }) {
      const prefix = context.dataset.label ? `${context.dataset.label}: ` : ''
      return context.parsed.y === null ? prefix : `${prefix}${context.parsed.y}%`
    },
  },
}

const legend = (token: GlobalToken) => ({
  position: 'top' as const,
  align: 'end' as const,
  labels: {
    font: { size: 11, weight: 500 as const },
    usePointStyle: true,
    pointStyle: 'circle' as const,
    padding: 12,
    color: token.colorTextSecondary,
  },
})

const scales = (token: GlobalToken, maxTicksLimit?: number) => ({
  y: {
    beginAtZero: true,
    max: 100,
    ticks: {
      callback: (value: string | number) => `${value}%`,
      font: { size: 11 },
      color: token.colorTextTertiary,
    },
    grid: { color: token.colorBorderSecondary },
    border: { display: false },
  },
  x: {
    ticks: {
      maxRotation: 0,
      autoSkip: true,
      maxTicksLimit,
      font: { size: 10 },
      color: token.colorTextTertiary,
    },
    grid: { display: false },
    border: { display: false },
  },
})

export const createChartOptions = (token: GlobalToken) => ({
  responsive: true,
  maintainAspectRatio: false,
  interaction: { mode: 'index' as const, intersect: false },
  plugins: {
    legend: legend(token),
    tooltip,
    zoom: undefined,
  },
  scales: scales(token, 8),
}) satisfies ChartOptions<'line'> & ChartOptions<'bar'>

export const createTaskBarOptions = (
  chartOptions: ReturnType<typeof createChartOptions>,
) => ({
  ...chartOptions,
  scales: { y: { beginAtZero: true } },
}) satisfies ChartOptions<'bar'>

export const createDiskBarOptions = (
  chartOptions: ReturnType<typeof createChartOptions>,
) => ({
  ...chartOptions,
  scales: { y: { beginAtZero: true, max: 100 } },
}) satisfies ChartOptions<'bar'>

export const createWorkerDetailOptions = (token: GlobalToken) => ({
  responsive: true,
  maintainAspectRatio: false,
  interaction: { mode: 'index' as const, intersect: false },
  plugins: {
    legend: legend(token),
    tooltip,
    zoom: {
      pan: { enabled: true, mode: 'x' as const },
      zoom: {
        wheel: { enabled: true },
        pinch: { enabled: true },
        mode: 'x' as const,
      },
    },
  },
  scales: scales(token),
}) satisfies ChartOptions<'line'>
