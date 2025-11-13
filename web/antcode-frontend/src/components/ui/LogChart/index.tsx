import React, { useState, useEffect } from 'react'
import { Card, Statistic, Row, Col, Tooltip, Progress, theme } from 'antd'
import { CheckCircleOutlined, ExclamationCircleOutlined, InfoCircleOutlined } from '@ant-design/icons'
import { useThemeContext } from '@/contexts/ThemeContext'
import styles from './LogChart.module.css'

interface LogStatistics {
  normal: number
  error: number
  warning: number
  total: number
}

interface LogChartProps {
  statistics: LogStatistics
  size?: number
  strokeWidth?: number
  className?: string
  responsive?: boolean
  onSegmentClick?: (type: 'normal' | 'warning' | 'error', count: number) => void
  showTooltip?: boolean
  title?: string
}

// 数值动画钩子
const useAnimatedNumber = (value: number, duration: number = 1000) => {
  const [animatedValue, setAnimatedValue] = useState(0)

  useEffect(() => {
    let startTime: number
    let animationFrame: number

    const animate = (currentTime: number) => {
      if (!startTime) startTime = currentTime
      const elapsed = currentTime - startTime
      const progress = Math.min(elapsed / duration, 1)
      
      // 使用缓动函数
      const easeOutCubic = 1 - Math.pow(1 - progress, 3)
      setAnimatedValue(Math.floor(value * easeOutCubic))

      if (progress < 1) {
        animationFrame = requestAnimationFrame(animate)
      }
    }

    animationFrame = requestAnimationFrame(animate)
    return () => cancelAnimationFrame(animationFrame)
  }, [value, duration])

  return animatedValue
}

const LogChart: React.FC<LogChartProps> = ({
  statistics,
  size = 200,
  strokeWidth = 12,
  className,
  responsive = true,
  onSegmentClick,
  showTooltip = true,
  title = '日志统计'
}) => {
  const { normal, error, warning, total } = statistics
  const { token } = theme.useToken() // 使用Ant Design主题token
  const { isDark } = useThemeContext() // 使用主题上下文
  
  // 交互状态
  const [hoveredSegment, setHoveredSegment] = useState<string | null>(null)
  
  // 响应式尺寸
  const [chartSize, setChartSize] = useState(size)
  const [chartStrokeWidth, setChartStrokeWidth] = useState(strokeWidth)
  
  useEffect(() => {
    if (!responsive) return
    
    const updateSize = () => {
      const width = window.innerWidth
      if (width < 480) {
        setChartSize(140)
        setChartStrokeWidth(8)
      } else if (width < 768) {
        setChartSize(160)
        setChartStrokeWidth(10)
      } else {
        setChartSize(size)
        setChartStrokeWidth(strokeWidth)
      }
    }
    
    updateSize()
    window.addEventListener('resize', updateSize)
    return () => window.removeEventListener('resize', updateSize)
  }, [responsive, size, strokeWidth])
  
  // 动画数值
  const animatedNormal = useAnimatedNumber(normal, 1500)
  const animatedError = useAnimatedNumber(error, 1500)
  const animatedWarning = useAnimatedNumber(warning, 1500)
  const animatedTotal = useAnimatedNumber(total, 1200)
  
  if (total === 0) {
    return (
      <Card 
        className={`${styles.logChart} ${className || ''}`} 
        styles={{ body: { textAlign: 'center', padding: '20px' } }}
      >
        <div className={styles.emptyState}>
          <InfoCircleOutlined style={{ fontSize: '32px', color: '#d9d9d9', marginBottom: '8px' }} />
          <div style={{ color: '#999', fontSize: '14px' }}>暂无日志数据</div>
        </div>
      </Card>
    )
  }

  const radius = (chartSize - chartStrokeWidth) / 2
  const circumference = 2 * Math.PI * radius
  const center = chartSize / 2
  
  // 计算百分比
  const normalPercent = (normal / total) * 100
  const errorPercent = (error / total) * 100
  const warningPercent = (warning / total) * 100
  
  // 计算弧长，为圆角效果预留间隙
  const gap = 2 // 弧段间隙
  const totalGaps = (normal > 0 ? 1 : 0) + (warning > 0 ? 1 : 0) + (error > 0 ? 1 : 0)
  const availableCircumference = circumference - (totalGaps > 1 ? (totalGaps * gap) : 0)
  
  const normalArcLength = normal > 0 ? (normal / total) * availableCircumference : 0
  const errorArcLength = error > 0 ? (error / total) * availableCircumference : 0  
  const warningArcLength = warning > 0 ? (warning / total) * availableCircumference : 0
  
  // 计算累积偏移量，包含间隙
  let currentOffset = 0
  const normalOffset = currentOffset
  if (normal > 0) {
    currentOffset += normalArcLength + gap
  }
  const warningOffset = currentOffset
  if (warning > 0) {
    currentOffset += warningArcLength + gap
  }
  const errorOffset = currentOffset

  return (
    <Card 
      className={`${styles.logChart} ${className || ''}`}
      styles={{ 
        body: { 
          padding: '20px',
          background: 'transparent'
        }
      }}
      title={title}
      variant="borderless"
      style={{
        background: 'transparent',
        boxShadow: 'none'
      }}
    >
      <div className={styles.chartContainer}>
        <div className={styles.svgContainer}>
          <svg width={chartSize} height={chartSize} className={styles.svg}>
            {/* 背景圆环 */}
            <circle
              cx={center}
              cy={center}
              r={radius}
              fill="none"
              stroke={isDark ? '#424242' : '#f0f0f0'}
              strokeWidth={chartStrokeWidth}
            />
            
            {/* 正常日志弧 */}
            {normal > 0 && (
              <Tooltip 
                title={showTooltip ? `正常日志: ${normal} 条 (${normalPercent.toFixed(1)}%)` : ''} 
                placement="top"
              >
                <circle
                  cx={center}
                  cy={center}
                  r={radius}
                  fill="none"
                  stroke={hoveredSegment === 'normal' ? token.colorSuccessActive : token.colorSuccess}
                  strokeWidth={hoveredSegment === 'normal' ? chartStrokeWidth + 2 : chartStrokeWidth}
                  strokeDasharray={`${normalArcLength} ${circumference}`}
                  strokeDashoffset={-normalOffset}
                  strokeLinecap="round"
                  className={`${styles.arc} ${styles.interactiveArc}`}
                  onMouseEnter={() => setHoveredSegment('normal')}
                  onMouseLeave={() => setHoveredSegment(null)}
                  onClick={() => onSegmentClick?.('normal', normal)}
                  style={{ cursor: onSegmentClick ? 'pointer' : 'default' }}
                />
              </Tooltip>
            )}
            
            {/* 警告日志弧 */}
            {warning > 0 && (
              <Tooltip 
                title={showTooltip ? `警告日志: ${warning} 条 (${warningPercent.toFixed(1)}%)` : ''} 
                placement="top"
              >
                <circle
                  cx={center}
                  cy={center}
                  r={radius}
                  fill="none"
                  stroke={hoveredSegment === 'warning' ? token.colorWarningActive : token.colorWarning}
                  strokeWidth={hoveredSegment === 'warning' ? chartStrokeWidth + 2 : chartStrokeWidth}
                  strokeDasharray={`${warningArcLength} ${circumference}`}
                  strokeDashoffset={-warningOffset}
                  strokeLinecap="round"
                  className={`${styles.arc} ${styles.interactiveArc}`}
                  onMouseEnter={() => setHoveredSegment('warning')}
                  onMouseLeave={() => setHoveredSegment(null)}
                  onClick={() => onSegmentClick?.('warning', warning)}
                  style={{ cursor: onSegmentClick ? 'pointer' : 'default' }}
                />
              </Tooltip>
            )}
            
            {/* 错误日志弧 */}
            {error > 0 && (
              <Tooltip 
                title={showTooltip ? `错误日志: ${error} 条 (${errorPercent.toFixed(1)}%)` : ''} 
                placement="top"
              >
                <circle
                  cx={center}
                  cy={center}
                  r={radius}
                  fill="none"
                  stroke={hoveredSegment === 'error' ? token.colorErrorActive : token.colorError}
                  strokeWidth={hoveredSegment === 'error' ? chartStrokeWidth + 2 : chartStrokeWidth}
                  strokeDasharray={`${errorArcLength} ${circumference}`}
                  strokeDashoffset={-errorOffset}
                  strokeLinecap="round"
                  className={`${styles.arc} ${styles.interactiveArc}`}
                  onMouseEnter={() => setHoveredSegment('error')}
                  onMouseLeave={() => setHoveredSegment(null)}
                  onClick={() => onSegmentClick?.('error', error)}
                  style={{ cursor: onSegmentClick ? 'pointer' : 'default' }}
                />
              </Tooltip>
            )}
            
            {/* 中心文本 */}
            <text
              x={center}
              y={center - 10}
              textAnchor="middle"
              className={styles.totalText}
            >
              {animatedTotal}
            </text>
            <text
              x={center}
              y={center + 15}
              textAnchor="middle"
              className={styles.labelText}
            >
              总计
            </text>
          </svg>
        </div>
        
        {/* 统计信息 */}
        <Row gutter={[8, 8]} className={styles.statsContainer}>
          <Col span={8}>
            <div 
              className={`${styles.statItem} ${hoveredSegment === 'normal' ? styles.highlighted : ''}`}
              onMouseEnter={() => setHoveredSegment('normal')}
              onMouseLeave={() => setHoveredSegment(null)}
              onClick={() => onSegmentClick?.('normal', normal)}
              style={{ cursor: onSegmentClick ? 'pointer' : 'default' }}
            >
              <CheckCircleOutlined style={{ color: token.colorSuccess, fontSize: '16px' }} />
              <div className={styles.statContent}>
                <div className={styles.statValue}>{animatedNormal}</div>
                <div className={styles.statLabel}>正常</div>
                <div className={styles.statPercent}>{normalPercent.toFixed(1)}%</div>
              </div>
            </div>
          </Col>
          <Col span={8}>
            <div 
              className={`${styles.statItem} ${hoveredSegment === 'warning' ? styles.highlighted : ''}`}
              onMouseEnter={() => setHoveredSegment('warning')}
              onMouseLeave={() => setHoveredSegment(null)}
              onClick={() => onSegmentClick?.('warning', warning)}
              style={{ cursor: onSegmentClick ? 'pointer' : 'default' }}
            >
              <ExclamationCircleOutlined style={{ color: token.colorWarning, fontSize: '16px' }} />
              <div className={styles.statContent}>
                <div className={styles.statValue}>{animatedWarning}</div>
                <div className={styles.statLabel}>警告</div>
                <div className={styles.statPercent}>{warningPercent.toFixed(1)}%</div>
              </div>
            </div>
          </Col>
          <Col span={8}>
            <div 
              className={`${styles.statItem} ${hoveredSegment === 'error' ? styles.highlighted : ''}`}
              onMouseEnter={() => setHoveredSegment('error')}
              onMouseLeave={() => setHoveredSegment(null)}
              onClick={() => onSegmentClick?.('error', error)}
              style={{ cursor: onSegmentClick ? 'pointer' : 'default' }}
            >
              <InfoCircleOutlined style={{ color: token.colorError, fontSize: '16px' }} />
              <div className={styles.statContent}>
                <div className={styles.statValue}>{animatedError}</div>
                <div className={styles.statLabel}>异常</div>
                <div className={styles.statPercent}>{errorPercent.toFixed(1)}%</div>
              </div>
            </div>
          </Col>
        </Row>
      </div>
    </Card>
  )
}

export default LogChart