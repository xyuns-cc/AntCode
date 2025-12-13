import React from 'react'
import { Table, Tooltip } from 'antd'
import type { TableProps, TableColumnType } from 'antd'
import styles from './ResponsiveTable.module.css'

interface ResponsiveTableProps<T> extends TableProps<T> {
  minWidth?: number // 表格最小宽度
  fixedActions?: boolean // 是否固定操作列
  showIndex?: boolean // 是否显示序号列
}

// 可排序值类型，用于排序函数内部类型转换
type SortableValue = string | number | Date | null | undefined

function ResponsiveTable<T extends object = Record<string, unknown>>({
  minWidth = 800,
  fixedActions = true,
  showIndex = true,
  columns,
  scroll,
  pagination,
  ...restProps
}: ResponsiveTableProps<T>) {
  // 获取当前分页信息
  const currentPage = typeof pagination === 'object' ? (pagination.current || 1) : 1
  const pageSize = typeof pagination === 'object' ? (pagination.pageSize || 10) : 10

  // 处理列配置
  const processedColumns = React.useMemo(() => {
    if (!columns) return columns

    // 处理现有列，添加排序和溢出省略
    const enhancedColumns = columns.map((col) => {
      const newCol: TableColumnType<T> = { ...(col as TableColumnType<T>) }

      // 如果是操作列且需要固定
      if (fixedActions && (col.key === 'actions' || col.dataIndex === 'actions')) {
        newCol.fixed = 'right'
        newCol.className = `${col.className || ''} table-actions-column`.trim()
      } else {
        // 非操作列添加排序功能（如果没有自定义 sorter）
        if (col.dataIndex && col.sorter === undefined && col.key !== 'index') {
          newCol.sorter = (a: T, b: T) => {
            const aVal = a[col.dataIndex as keyof T] as SortableValue
            const bVal = b[col.dataIndex as keyof T] as SortableValue
            
            // 处理 null/undefined
            if (aVal == null && bVal == null) return 0
            if (aVal == null) return -1
            if (bVal == null) return 1
            
            // 日期类型
            if (aVal instanceof Date && bVal instanceof Date) {
              return aVal.getTime() - bVal.getTime()
            }
            
            // 字符串日期格式
            if (typeof aVal === 'string' && typeof bVal === 'string') {
              const dateA = Date.parse(aVal)
              const dateB = Date.parse(bVal)
              if (!isNaN(dateA) && !isNaN(dateB)) {
                return dateA - dateB
              }
            }
            
            // 数字类型
            if (typeof aVal === 'number' && typeof bVal === 'number') {
              return aVal - bVal
            }
            
            // 字符串类型
            return String(aVal).localeCompare(String(bVal), 'zh-CN')
          }
          newCol.sortDirections = ['ascend', 'descend']
        }

        // 非操作列添加溢出省略和气泡提示（如果没有自定义 render 且没有禁用）
        if (!col.render && col.ellipsis !== false && col.key !== 'index') {
          newCol.ellipsis = { showTitle: false }
          newCol.render = (text: React.ReactNode) => {
            if (text == null || text === '') return '-'
            const displayText = String(text)
            return (
              <Tooltip title={displayText} placement="topLeft">
                <span>{displayText}</span>
              </Tooltip>
            )
          }
        }
      }

      return newCol
    })

    // 添加序号列
    if (showIndex) {
      const indexColumn: TableColumnType<T> = {
        title: '序号',
        key: 'index',
        width: 70,
        fixed: 'left',
        render: (_: unknown, __: T, index: number) => (currentPage - 1) * pageSize + index + 1
      }
      return [indexColumn, ...enhancedColumns]
    }

    return enhancedColumns
  }, [columns, fixedActions, showIndex, currentPage, pageSize])

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
        pagination={pagination}
        className={`${restProps.className || ''}`.trim()}
        showSorterTooltip={{ title: '点击排序' }}
      />
    </div>
  )
}

// 直接导出泛型组件，保留类型信息
export default ResponsiveTable
