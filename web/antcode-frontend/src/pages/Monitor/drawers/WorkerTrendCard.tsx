import { Button, Card, Empty, theme } from 'antd'
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
      {/* data 为 null = 这台机器既没有历史点也没上报过当前读数（见 ../charts/data）。
          留一张空白画布会被读成「用量一直是 0」或「图还没加载出来」。 */}
      <div style={{ height: 250 }}>
        {data
          ? <Line ref={chartRef} data={data} options={options} />
          : <Empty description="该 Worker 尚未上报过资源指标" />}
      </div>
    </Card>
  )
}
