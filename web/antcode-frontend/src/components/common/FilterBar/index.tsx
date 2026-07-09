import type React from 'react'
import styles from './FilterBar.module.css'

const cx = (...names: (string | false | null | undefined)[]) =>
  names.filter(Boolean).join(' ')

export interface FilterBarProps {
  /** 左侧筛选控件（Search / Select / DatePicker / Tag 等），数组或片段 */
  filters?: React.ReactNode
  /** 右侧操作按钮（刷新 / 新建 / 批量删除等） */
  actions?: React.ReactNode
  /** 自定义 class */
  className?: string
}

/**
 * 统一筛选/工具栏。左侧 filters 自动 wrap，右侧 actions 始终右对齐。
 *
 * 示例：
 *   <FilterBar
 *     filters={<>
 *       <Search ... />
 *       <Select ... />
 *     </>}
 *     actions={<>
 *       <Button icon={<ReloadOutlined />}>刷新</Button>
 *       <Button type="primary">新建</Button>
 *     </>}
 *   />
 */
const FilterBar: React.FC<FilterBarProps> = ({ filters, actions, className }) => {
  return (
    <div className={cx(styles.root, className)}>
      {filters && <div className={styles.filters}>{filters}</div>}
      {actions && <div className={styles.actions}>{actions}</div>}
    </div>
  )
}

export default FilterBar
