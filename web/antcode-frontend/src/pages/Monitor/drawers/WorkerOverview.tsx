import { Descriptions, Progress, Tag, Tooltip } from 'antd'
import type { ReactNode } from 'react'
import { getStatusColor, getStatusText } from '../status'
import type { WorkerDisplayData } from '../types'
import { usageBarColor, usageReading, usageText } from '../usage'

const GB = 1024 * 1024 * 1024

const bytesToGb = (value?: number) => ((value || 0) / GB).toFixed(2)

// value 为 null 时条形留空且不涂色，百分比位写占位符（见 ../usage）。
const ResourceProgress = ({ value, normalColor, details }: {
  value: number | null
  normalColor: string
  details?: ReactNode
}) => (
  <div style={{ width: '100%' }}>
    <Tooltip title={details}>
      <Progress
        percent={value ?? 0}
        size="small"
        strokeColor={usageBarColor(value, normalColor)}
        format={() => usageText(value)}
      />
    </Tooltip>
  </div>
)

export const WorkerOverview = ({ worker }: { worker: WorkerDisplayData }) => (
  <Descriptions column={2} bordered size="small" labelStyle={{ width: 100 }} contentStyle={{ width: 150 }}>
    <Descriptions.Item label="Worker 名称" span={2}>{worker.name}</Descriptions.Item>
    <Descriptions.Item label="地址"><span style={{ fontFamily: 'monospace' }}>{worker.host}:{worker.port}</span></Descriptions.Item>
    <Descriptions.Item label="版本">{worker.version}</Descriptions.Item>
    <Descriptions.Item label="状态"><Tag color={getStatusColor(worker.status)}>{getStatusText(worker.status)}</Tag></Descriptions.Item>
    <Descriptions.Item label="运行时间">{worker.uptime}</Descriptions.Item>
    <Descriptions.Item label="CPU使用率">
      <ResourceProgress
        value={usageReading(worker, 'cpu')}
        normalColor="#1890ff"
        details={worker.cpuCores ? <div>核心数: {worker.cpuCores} 核</div> : undefined}
      />
    </Descriptions.Item>
    <Descriptions.Item label="内存使用率">
      <ResourceProgress
        value={usageReading(worker, 'memory')}
        normalColor="#52c41a"
        details={worker.memoryTotal ? <div><div>总内存: {bytesToGb(worker.memoryTotal)} GB</div><div>已使用: {bytesToGb(worker.memoryUsed)} GB</div><div>可用: {bytesToGb(worker.memoryAvailable)} GB</div></div> : undefined}
      />
    </Descriptions.Item>
    <Descriptions.Item label="磁盘使用率">
      <ResourceProgress
        value={usageReading(worker, 'disk')}
        normalColor="#722ed1"
        details={worker.diskTotal ? <div><div>总容量: {bytesToGb(worker.diskTotal)} GB</div><div>已使用: {bytesToGb(worker.diskUsed)} GB</div><div>可用: {bytesToGb(worker.diskFree)} GB</div></div> : undefined}
      />
    </Descriptions.Item>
    <Descriptions.Item label="任务数量">{worker.tasks}个</Descriptions.Item>
    {worker.lastHeartbeat && (
      <Descriptions.Item label="最后心跳" span={2}>{new Date(worker.lastHeartbeat).toLocaleString('zh-CN')}</Descriptions.Item>
    )}
  </Descriptions>
)
