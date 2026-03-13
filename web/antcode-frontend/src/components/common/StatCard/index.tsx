import type React from 'react'
import { memo } from 'react'
import { Skeleton, Typography, theme } from 'antd'
import styles from './StatCard.module.css'

const { Text } = Typography

export interface StatCardProps {
  title: string
  value: string | number
  subValue?: string
  icon: React.ReactNode
  iconColor: string
  loading?: boolean
}

const StatCard: React.FC<StatCardProps> = memo(({ title, value, subValue, icon, iconColor, loading }) => {
  const { token } = theme.useToken()

  const cssVars = {
    '--stat-card-accent': iconColor,
    '--stat-card-border': token.colorBorderSecondary,
  } as React.CSSProperties

  return (
    <Skeleton loading={loading} active paragraph={{ rows: 1 }}>
      <div
        className={styles.statCard}
        style={{ background: token.colorBgContainer, ...cssVars }}
      >
        <div className={styles.decorCircle} />
        <div className={styles.iconBox}>{icon}</div>
        <div className={styles.content}>
          <Text type="secondary" className={styles.title}>{title}</Text>
          <span className={styles.value} style={{ color: token.colorText }}>{value}</span>
          {subValue && (
            <Text type="secondary" className={styles.subValue}>{subValue}</Text>
          )}
        </div>
      </div>
    </Skeleton>
  )
})

StatCard.displayName = 'StatCard'

export default StatCard
