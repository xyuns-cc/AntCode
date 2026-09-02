import type React from 'react'
import { memo } from 'react'
import { Flex, Typography, theme } from 'antd'

const { Text } = Typography

/** 读数缺失时的占位。「没拿到」必须和「值是 0」长得不一样。 */
export const NO_METRIC = '—'

interface MetricCardProps {
  title: string
  /** null / undefined = 这项没拿到；0 是真的 0。两者必须分开渲染。 */
  value: number | string | null | undefined
  suffix?: string
  subValue?: string
  icon: React.ReactNode
  accentColor: string
  trend?: number
}

/**
 * 单个标量指标卡。
 *
 * 它只负责一件事：把**一个可能缺失的标量**变成可读的显示值。这条职责独有的失效模式就是
 * 把「没拿到」折算成 0 —— 调用方过去写 `stats?.totalErrors || 0`，于是一次
 * /workers/cluster/spider-stats 失败会渲染成「请求 0、错误 0、延迟 0ms」的满绿面板，
 * 和「集群很闲」一模一样。宿主 SpiderStatsTab 的失效模式是另一回事（拉取、轮询节拍、
 * 图表编排），所以这层单独拆出来。
 */
const MetricCard = memo(({ title, value, suffix, subValue, icon, accentColor, trend }: MetricCardProps) => {
  const { token } = theme.useToken()
  return (
    <div style={{
      background: token.colorBgContainer,
      border: `1px solid ${token.colorBorderSecondary}`,
      borderRadius: 12,
      padding: '14px 16px',
      position: 'relative',
      overflow: 'hidden',
      height: 110,
      transition: 'border-color 0.2s ease, box-shadow 0.2s ease'
    }}>
      {/* 右上角装饰大圆 */}
      <div style={{
        position: 'absolute',
        right: -20,
        top: -20,
        width: 80,
        height: 80,
        borderRadius: '50%',
        background: `${accentColor}10`
      }} />
      {/* 右上角方形圆角图标 */}
      <div style={{
        position: 'absolute',
        right: 12,
        top: 12,
        width: 36,
        height: 36,
        borderRadius: 10,
        background: `${accentColor}20`,
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'center',
        fontSize: 16,
        color: accentColor,
        zIndex: 1
      }}>
        {icon}
      </div>
      {/* 内容区 */}
      <div style={{ paddingRight: 50, position: 'relative', zIndex: 1 }}>
        <Text type="secondary" style={{ fontSize: 12, display: 'block', marginBottom: 6 }}>{title}</Text>
        <Flex align="baseline" gap={4}>
          <span style={{ color: token.colorText, fontSize: 24, fontWeight: 600, lineHeight: 1 }}>
            {value == null ? NO_METRIC : typeof value === 'number' ? value.toLocaleString() : value}
          </span>
          {suffix && <Text type="secondary" style={{ fontSize: 12 }}>{suffix}</Text>}
        </Flex>
        {/* subValue 和 trend 放在同一行 */}
        <Flex align="center" gap={8} style={{ marginTop: 6 }}>
          {subValue && <Text type="secondary" style={{ fontSize: 11 }}>{subValue}</Text>}
          {trend !== undefined && (
            <span style={{ color: trend >= 0 ? token.colorSuccess : token.colorError, fontSize: 11 }}>
              {trend >= 0 ? '+' : ''}{trend.toFixed(1)}% <Text type="secondary" style={{ fontSize: 11 }}>较上分钟</Text>
            </span>
          )}
        </Flex>
      </div>
    </div>
  )
})

export default MetricCard
