import type { FC } from 'react'
import { Col, Row } from 'antd'
import type { LogStats, ViewerTheme } from './enhancedLogViewerTypes'
import { buildStatsItems } from './enhancedLogViewerUtils'

interface StatsProps {
  filteredCount: number
  stats: LogStats
  token: ViewerTheme
}

const EnhancedLogViewerStats: FC<StatsProps> = ({ filteredCount, stats, token }) => (
  <div style={{
    marginBottom: 20,
    padding: '16px 20px',
    background: token.colorFillQuaternary,
    borderRadius: 6,
    border: `1px solid ${token.colorBorder}`,
  }}>
    <Row gutter={[16, 16]}>
      {buildStatsItems(stats, filteredCount, token).map((item) => (
        <Col key={item.key} xs={12} sm={8} md={4}>
          <div style={{ textAlign: 'center' }}>
            <div style={{
              fontSize: 24,
              fontWeight: 700,
              color: item.color || token.colorText,
              lineHeight: 1.2,
              marginBottom: 4,
            }}>{item.value}</div>
            <div style={{ fontSize: 12, color: token.colorTextTertiary, fontWeight: 500 }}>
              {item.title}
            </div>
          </div>
        </Col>
      ))}
    </Row>
  </div>
)

export default EnhancedLogViewerStats
