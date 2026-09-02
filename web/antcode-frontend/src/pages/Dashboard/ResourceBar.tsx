import { memo } from 'react'
import { Flex, Progress, Typography, theme } from 'antd'
import { NO_DATA } from './present'

const { Text } = Typography

interface ResourceBarProps {
  label: string
  /** null = 指标没拿到（/dashboard/metrics 是管理员专属）。不是「占用 0%」。 */
  value: number | null
  color: string
}

/** 资源占用进度条。只负责画，不做归一化——取值范围由调用方保证。 */
const ResourceBar: React.FC<ResourceBarProps> = memo(({ label, value, color }) => {
  const { token } = theme.useToken()
  return (
    <div style={{ marginBottom: 16 }}>
      <Flex justify="space-between" style={{ marginBottom: 6 }}>
        <Text style={{ fontSize: 13 }}>{label}</Text>
        <Text strong style={{ fontSize: 13 }}>{value === null ? NO_DATA : `${value}%`}</Text>
      </Flex>
      <Progress
        percent={value ?? 0}
        showInfo={false}
        // 没拿到读数时不能涂色：一条彩色的空条看起来就是「占用很低」。
        strokeColor={value === null ? token.colorFillSecondary : color}
        trailColor={token.colorFillSecondary}
        size="small"
      />
    </div>
  )
})

ResourceBar.displayName = 'ResourceBar'

export default ResourceBar
