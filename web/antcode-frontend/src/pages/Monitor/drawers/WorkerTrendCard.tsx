import { Button, Card, theme } from 'antd'
import type { Chart, ChartData, ChartOptions } from 'chart.js'
import type { RefObject } from 'react'
import { Line } from 'react-chartjs-2'

interface WorkerTrendCardProps {
  chartRef: RefObject<Chart<'line'> | null>
  data: ChartData<'line'> | null
  options: ChartOptions<'line'>
}

export const WorkerTrendCard = ({ chartRef, data, options }: WorkerTrendCardProps) => {
  const { token } = theme.useToken()
  const title = (
    <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
      <span>资源使用趋势（30天）</span>
      <Button size="small" type="link" onClick={() => chartRef.current?.resetZoom()} style={{ fontSize: 11 }}>
        重置缩放
      </Button>
    </div>
  )
  return (
    <Card
      title={title}
      style={{ marginTop: 16 }}
      size="small"
      extra={<span style={{ fontSize: 11, color: token.colorTextTertiary }}>💡 滚轮缩放 · 拖拽平移</span>}
    >
      <div style={{ height: 250 }}>
        {data && <Line ref={chartRef} data={data} options={options} />}
      </div>
    </Card>
  )
}
