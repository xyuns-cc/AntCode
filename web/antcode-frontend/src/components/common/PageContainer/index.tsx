import type React from 'react'
import styles from './PageContainer.module.css'

const cx = (...names: (string | false | null | undefined)[]) =>
  names.filter(Boolean).join(' ')

export interface PageContainerProps {
  /** 标题，渲染在顶部 header 区域 */
  title?: React.ReactNode
  /** 标题右侧操作区（按钮组等） */
  extra?: React.ReactNode
  /** 标题下方、toolbar 上方的横幅区域（统计卡片等） */
  banner?: React.ReactNode
  /** 筛选/工具栏（建议使用 FilterBar 组件） */
  toolbar?: React.ReactNode
  /** body 区是否可滚动；true 用于非表格的长内容页（详情/表单/Dashboard） */
  scrollable?: boolean
  /** 紧凑模式：缩小内外边距 */
  compact?: boolean
  /** 自定义 body class（极少需要） */
  bodyClassName?: string
  /** 自定义 root class */
  className?: string
  children?: React.ReactNode
}

/**
 * 统一页面骨架。
 *
 * - 默认（`scrollable=false`）：body 区不滚动，由内部组件（如 ResponsiveTable fill 模式）自管滚动，
 *   适合纯表格页。
 * - `scrollable=true`：body 区垂直滚动，适合 Dashboard / 详情 / 表单等长内容。
 */
const PageContainer: React.FC<PageContainerProps> = ({
  title,
  extra,
  banner,
  toolbar,
  scrollable = false,
  compact = false,
  bodyClassName,
  className,
  children,
}) => {
  const hasHeader = title !== undefined || extra !== undefined
  return (
    <div className={cx(styles.root, compact && styles.compact, className)}>
      <div className={styles.card}>
        {hasHeader && (
          <div className={styles.header}>
            <div className={styles.headerTitle}>{title}</div>
            {extra && <div className={styles.headerExtra}>{extra}</div>}
          </div>
        )}
        {banner && <div className={styles.banner}>{banner}</div>}
        {toolbar && <div className={styles.toolbar}>{toolbar}</div>}
        <div
          className={cx(
            styles.body,
            scrollable ? styles.bodyScroll : styles.bodyFill,
            bodyClassName
          )}
        >
          {children}
        </div>
      </div>
    </div>
  )
}

export default PageContainer
