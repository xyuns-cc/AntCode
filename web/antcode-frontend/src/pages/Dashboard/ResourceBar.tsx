import { memo } from 'react'
import { Flex, Progress, Typography, theme } from 'antd'

const { Text } = Typography

interface ResourceBarProps {
  label: string
  value: number
  color: string
}

/** 资源占用进度条。只负责画，不做归一化——取值范围由调用方保证。 */
const ResourceBar: React.FC<ResourceBarProps> = memo(({ label, value, color }) => {
  const { token } = theme.useToken()
  return (
    <div style={{ marginBottom: 16 }}>
      <Flex justify="space-between" style={{ marginBottom: 6 }}>
        <Text style={{ fontSize: 13 }}>{label}</Text>
        <Text strong style={{ fontSize: 13 }}>{value}%</Text>
      </Flex>
      <Progress
        percent={value}
        showInfo={false}
        strokeColor={color}
        trailColor={token.colorFillSecondary}
        size="small"
      />
    </div>
  )
})

ResourceBar.displayName = 'ResourceBar'

export default ResourceBar
