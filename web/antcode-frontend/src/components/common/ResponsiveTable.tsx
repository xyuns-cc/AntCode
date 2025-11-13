import React from 'react'
import { Table } from 'antd'
import type { TableProps } from 'antd'
import styles from './ResponsiveTable.module.css'

// 浅比较函数
function shallowEqual<T extends Record<string, unknown>>(
  prevProps: T,
  nextProps: T
): boolean {
  const prevKeys = Object.keys(prevProps)
  const nextKeys = Object.keys(nextProps)

  if (prevKeys.length !== nextKeys.length) {
    return false
  }

  for (const key of prevKeys) {
    if (prevProps[key] !== nextProps[key]) {
      return false
    }
  }

  return true
}

interface ResponsiveTableProps<T> extends TableProps<T> {
  minWidth?: number // 表格最小宽度
  fixedActions?: boolean // 是否固定操作列
}

function ResponsiveTable<T extends object = Record<string, unknown>>({
  minWidth = 800,
  fixedActions = true,
  columns,
  scroll,
  ...restProps
}: ResponsiveTableProps<T>) {
  // 处理列配置，确保操作列固定在右侧
  const processedColumns = React.useMemo(() => {
    if (!columns) return columns

    return columns.map((col) => {
      // 如果是操作列且需要固定
      if (fixedActions && (col.key === 'actions' || col.dataIndex === 'actions')) {
        return {
          ...col,
          fixed: 'right',
          className: `${col.className || ''} table-actions-column`.trim()
        }
      }
      return col
    })
  }, [columns, fixedActions])

  // 合并滚动配置
  const mergedScroll = React.useMemo(() => {
    return {
      x: minWidth,
      ...scroll
    }
  }, [minWidth, scroll])

  return (
    <div className={styles.container}>
      <Table<T>
        {...restProps}
        columns={processedColumns}
        scroll={mergedScroll}
        className={`${restProps.className || ''}`.trim()}
      />
    </div>
  )
}

// 使用React.memo优化，采用浅比较提升性能
export default React.memo(ResponsiveTable, (prevProps, nextProps) => {
  // 自定义比较函数，重点关注影响渲染的属性
  return (
    shallowEqual(
      {
        dataSource: prevProps.dataSource,
        loading: prevProps.loading,
        columns: prevProps.columns,
        pagination: prevProps.pagination,
        minWidth: prevProps.minWidth,
        fixedActions: prevProps.fixedActions
      },
      {
        dataSource: nextProps.dataSource,
        loading: nextProps.loading,
        columns: nextProps.columns,
        pagination: nextProps.pagination,
        minWidth: nextProps.minWidth,
        fixedActions: nextProps.fixedActions
      }
    )
  )
})
