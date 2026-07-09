import React from 'react'
import { ConfigProvider, Empty, Table, Tooltip } from 'antd'
import type { TableProps, TableColumnType } from 'antd'
import type { ColumnGroupType, ColumnType, ColumnsType } from 'antd/es/table'
import { InboxOutlined } from '@ant-design/icons'
import { useElementSize } from '@/hooks/useElementSize'
import styles from './ResponsiveTable.module.css'

const getBody = () => document.body

interface ResponsiveTableProps<T> extends TableProps<T> {
  minWidth?: number // 表格最小宽度（水平滚动阈值）
  fixedActions?: boolean // 是否固定操作列
  showIndex?: boolean // 是否显示序号列
  /**
   * fill 模式：表格自动撑满父容器（height: 100%），表头固定，表格 body Y 滚动；
   * 父容器必须有确定高度（如 PageContainer body fill 模式提供的）。
   */
  fill?: boolean
  /** 空数据时显示的文案（默认"暂无数据"） */
  emptyDescription?: React.ReactNode
  /** 空数据时显示的操作（如"新建"按钮），在 description 下方 */
  emptyAction?: React.ReactNode
  /**
   * 虚拟滚动阈值：dataSource 长度 ≥ 此值时自动开启 antd Table `virtual` 模式（默认 500）。
   * 传 `virtual={true/false}` 显式覆盖自动逻辑；传 `virtualThreshold={0}` 关闭自动。
   */
  virtualThreshold?: number
}

type SortableValue = string | number | Date | null | undefined

// fill 模式 scroll.y 的兜底值（无法实测时使用）；实际优先用 ResizeObserver 量得的真实高度。
const FALLBACK_HEADER_HEIGHT = 47
const FALLBACK_PAGINATION_HEIGHT = 56

function ResponsiveTable<T extends object = Record<string, unknown>>({
  minWidth = 800,
  fixedActions = true,
  showIndex = true,
  fill = false,
  emptyDescription = '暂无数据',
  emptyAction,
  virtualThreshold = 500,
  virtual,
  columns,
  scroll,
  pagination,
  locale,
  dataSource,
  ...restProps
}: ResponsiveTableProps<T>) {
  // 自动虚拟滚动：数据量大时启用，减少 DOM 节点数量。
  // 页面显式传 `virtual` 会覆盖自动逻辑（true/false 都尊重）。
  const autoVirtual = React.useMemo(() => {
    if (virtual !== undefined) return virtual
    if (virtualThreshold <= 0) return false
    const dataLen = Array.isArray(dataSource) ? dataSource.length : 0
    return dataLen >= virtualThreshold
  }, [virtual, virtualThreshold, dataSource])
  const currentPage = typeof pagination === 'object' ? (pagination.current || 1) : 1
  const pageSize = typeof pagination === 'object' ? (pagination.pageSize || 10) : 10

  const [containerRef, { height: containerHeight }] = useElementSize<HTMLDivElement>()
  const [theadHeight, setTheadHeight] = React.useState(FALLBACK_HEADER_HEIGHT)
  const [paginationHeight, setPaginationHeight] = React.useState(FALLBACK_PAGINATION_HEIGHT)

  // 实测表头与分页器高度，保证 scroll.y 把可用空间用满（避免表格下方留白）。
  // 用 rAF 节流，避免 ResizeObserver 触发风暴。
  React.useEffect(() => {
    if (!fill) return
    const root = containerRef.current
    if (!root) return
    let rafId = 0
    const measure = () => {
      const thead = root.querySelector<HTMLElement>('.ant-table-thead')
      const pagination = root.querySelector<HTMLElement>('.ant-table-pagination')
      if (thead && thead.offsetHeight > 0) {
        setTheadHeight((prev) => (prev === thead.offsetHeight ? prev : thead.offsetHeight))
      }
      if (pagination && pagination.offsetHeight > 0) {
        const cs = window.getComputedStyle(pagination)
        const marginTop = parseFloat(cs.marginTop) || 0
        const marginBottom = parseFloat(cs.marginBottom) || 0
        const total = pagination.offsetHeight + marginTop + marginBottom
        setPaginationHeight((prev) => (prev === total ? prev : total))
      }
    }
    const observer = new ResizeObserver(() => {
      if (rafId) cancelAnimationFrame(rafId)
      rafId = requestAnimationFrame(measure)
    })
    observer.observe(root)
    const thead = root.querySelector<HTMLElement>('.ant-table-thead')
    const pagination = root.querySelector<HTMLElement>('.ant-table-pagination')
    if (thead) observer.observe(thead)
    if (pagination) observer.observe(pagination)
    return () => {
      observer.disconnect()
      if (rafId) cancelAnimationFrame(rafId)
    }
  }, [fill, containerRef])

  // antd 5 在单页时不渲染"跳至 X 页"输入框；为了所有表格分页样式一致，单页时手动注入一个 disabled 占位。
  React.useEffect(() => {
    const root = containerRef.current
    if (!root) return

    const ensureJumper = () => {
      const pagination = root.querySelector<HTMLElement>('.ant-table-pagination')
      if (!pagination) return
      const native = pagination.querySelector('.ant-pagination-options-quick-jumper:not([data-fake-jumper])')
      const fake = pagination.querySelector('[data-fake-jumper]')
      if (native) {
        fake?.remove()
        return
      }
      if (fake) return
      const li = document.createElement('li')
      li.className = 'ant-pagination-options-quick-jumper'
      li.setAttribute('data-fake-jumper', 'true')
      const labelL = document.createElement('span')
      labelL.textContent = '跳至'
      const input = document.createElement('input')
      input.type = 'text'
      input.disabled = true
      input.value = '1'
      input.setAttribute('aria-label', '跳至页')
      const labelR = document.createElement('span')
      labelR.textContent = '页'
      li.append(labelL, input, labelR)
      pagination.appendChild(li)
    }

    const observer = new MutationObserver(ensureJumper)
    observer.observe(root, { childList: true, subtree: true })
    ensureJumper()
    return () => observer.disconnect()
  }, [containerRef])

  const processedColumns = React.useMemo(() => {
    if (!columns) return columns

    type AnyColumn = ColumnType<T> | ColumnGroupType<T>

    const enhanceColumn = (col: AnyColumn): AnyColumn => {
      if ('children' in col && col.children) {
        return {
          ...col,
          children: (col.children as ColumnsType<T>).map(enhanceColumn),
        }
      }

      const leafCol = col as ColumnType<T>
      const newCol: TableColumnType<T> = { ...leafCol }

      if (fixedActions && (newCol.key === 'actions' || newCol.dataIndex === 'actions')) {
        newCol.fixed = 'right'
        newCol.className = `${newCol.className || ''} table-actions-column`.trim()
      } else {
        if (newCol.dataIndex && newCol.sorter === undefined && newCol.key !== 'index') {
          newCol.sorter = (a: T, b: T) => {
            const aVal = a[newCol.dataIndex as keyof T] as SortableValue
            const bVal = b[newCol.dataIndex as keyof T] as SortableValue

            if (aVal == null && bVal == null) return 0
            if (aVal == null) return -1
            if (bVal == null) return 1

            if (aVal instanceof Date && bVal instanceof Date) {
              return aVal.getTime() - bVal.getTime()
            }

            if (typeof aVal === 'string' && typeof bVal === 'string') {
              const dateA = Date.parse(aVal)
              const dateB = Date.parse(bVal)
              if (!isNaN(dateA) && !isNaN(dateB)) {
                return dateA - dateB
              }
            }

            if (typeof aVal === 'number' && typeof bVal === 'number') {
              return aVal - bVal
            }

            return String(aVal).localeCompare(String(bVal), 'zh-CN')
          }
          newCol.sortDirections = ['ascend', 'descend']
        }

        if (!newCol.render && newCol.ellipsis !== false && newCol.key !== 'index') {
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
    }

    const enhancedColumns = (columns as ColumnsType<T>).map(enhanceColumn)

    if (showIndex) {
      const indexColumn: TableColumnType<T> = {
        title: '序号',
        key: 'index',
        width: 70,
        fixed: 'left',
        render: (_: unknown, __: T, index: number) =>
          (currentPage - 1) * pageSize + index + 1,
      }
      return [indexColumn, ...enhancedColumns]
    }

    return enhancedColumns
  }, [columns, fixedActions, showIndex, currentPage, pageSize])

  // fill 模式：根据父容器高度 - 表头高度 - 分页区高度，动态计算表格内部可滚区高度
  const computedScrollY = React.useMemo(() => {
    if (!fill || containerHeight <= 0) return undefined
    const hasPagination = pagination !== false
    const y = containerHeight - theadHeight - (hasPagination ? paginationHeight : 0)
    return Math.max(120, Math.floor(y))
  }, [fill, containerHeight, theadHeight, paginationHeight, pagination])

  const mergedScroll = React.useMemo(() => {
    return {
      x: minWidth,
      ...(computedScrollY !== undefined ? { y: computedScrollY } : {}),
      ...scroll,
    }
  }, [minWidth, computedScrollY, scroll])

  // 统一分页配置：page 只需传 current/pageSize/total/onChange/onShowSizeChange，
  // 视觉相关项（showSizeChanger / showQuickJumper / showTotal / pageSizeOptions）由 ResponsiveTable 统一注入。
  const mergedPagination = React.useMemo(() => {
    if (pagination === false) return false as const
    const defaults = {
      showSizeChanger: true,
      showQuickJumper: true,
      showTotal: (total: number, range: [number, number]) =>
        `第 ${range[0]}-${range[1]} 条，共 ${total} 条`,
      pageSizeOptions: ['10', '20', '50', '100'],
      defaultPageSize: 20,
      size: 'default' as const,
    }
    if (typeof pagination === 'object' && pagination !== null) {
      return { ...defaults, ...pagination }
    }
    return defaults
  }, [pagination])

  const containerClassName = fill
    ? `${styles.container} ${styles.fillWrapper}`
    : styles.container

  // 统一空状态：图标 + 文案 + 可选 action（如"新建"按钮）
  const mergedLocale = React.useMemo(
    () => ({
      ...locale,
      emptyText: (
        <Empty
          image={<InboxOutlined style={{ fontSize: 48, color: 'var(--ant-color-text-quaternary)' }} />}
          imageStyle={{ height: 60, display: 'flex', alignItems: 'center', justifyContent: 'center' }}
          description={<span style={{ color: 'var(--ant-color-text-tertiary)' }}>{emptyDescription}</span>}
        >
          {emptyAction}
        </Empty>
      ),
    }),
    [locale, emptyDescription, emptyAction]
  )

  return (
    <div ref={containerRef} className={containerClassName}>
      {/* 强制分页器内 Select/Tooltip 等弹层 portal 到 body，避免被 PageContainer 的 overflow:hidden 裁切。 */}
      <ConfigProvider getPopupContainer={getBody}>
        <Table<T>
          {...restProps}
          dataSource={dataSource}
          columns={processedColumns}
          scroll={mergedScroll}
          pagination={mergedPagination}
          locale={mergedLocale}
          virtual={autoVirtual}
          className={`${restProps.className || ''}`.trim()}
          showSorterTooltip={{ title: '点击排序' }}
        />
      </ConfigProvider>
    </div>
  )
}

export default ResponsiveTable
