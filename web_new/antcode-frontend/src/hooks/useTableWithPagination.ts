import { useState, useMemo, useCallback } from 'react'

/**
 * 表格分页与筛选通用 Hook
 * 整合了分页、搜索、筛选功能，减少页面组件的重复代码
 */

export interface TablePaginationConfig {
  current: number
  pageSize: number
  total: number
  onChange: (page: number, pageSize: number) => void
  onShowSizeChange: (current: number, size: number) => void
  showSizeChanger: boolean
  showQuickJumper: boolean
  showTotal: (total: number, range: [number, number]) => string
  pageSizeOptions: string[]
}

export interface UseTableWithPaginationOptions<T> {
  /** 搜索字段列表 */
  searchFields?: (keyof T)[]
  /** 初始页码 */
  initialPage?: number
  /** 初始每页条数 */
  initialPageSize?: number
  /** 每页条数选项 */
  pageSizeOptions?: string[]
  /** 自定义筛选函数 */
  filterFunctions?: Record<string, (item: T, value: unknown) => boolean>
}

export interface UseTableWithPaginationResult<T> {
  /** 分页后的数据 */
  paginatedData: T[]
  /** 筛选后的总数 */
  total: number
  /** 当前页码 */
  currentPage: number
  /** 每页条数 */
  pageSize: number
  /** 搜索关键词 */
  searchQuery: string
  /** 筛选条件 */
  filters: Record<string, unknown>
  /** 设置搜索关键词 */
  setSearchQuery: (query: string) => void
  /** 设置单个筛选条件 */
  setFilter: (key: string, value: unknown) => void
  /** 重置所有筛选条件 */
  resetFilters: () => void
  /** 设置页码 */
  setCurrentPage: (page: number) => void
  /** 设置每页条数 */
  setPageSize: (size: number) => void
  /** Ant Design Table 分页配置 */
  paginationConfig: TablePaginationConfig
  /** 处理分页变化 */
  handlePaginationChange: (page: number, size: number) => void
}

export function useTableWithPagination<T>(
  data: T[],
  options: UseTableWithPaginationOptions<T> = {}
): UseTableWithPaginationResult<T> {
  const {
    searchFields = [],
    initialPage = 1,
    initialPageSize = 20,
    pageSizeOptions = ['10', '20', '50', '100'],
    filterFunctions = {},
  } = options

  // 状态
  const [currentPage, setCurrentPage] = useState(initialPage)
  const [pageSize, setPageSize] = useState(initialPageSize)
  const [searchQuery, setSearchQueryState] = useState('')
  const [filters, setFilters] = useState<Record<string, unknown>>({})

  // 设置搜索关键词（重置页码）
  const setSearchQuery = useCallback((query: string) => {
    setSearchQueryState(query)
    setCurrentPage(1)
  }, [])

  // 设置单个筛选条件（重置页码）
  const setFilter = useCallback((key: string, value: unknown) => {
    setFilters(prev => {
      if (value === undefined || value === null || value === '') {
        const { [key]: _, ...rest } = prev
        return rest
      }
      return { ...prev, [key]: value }
    })
    setCurrentPage(1)
  }, [])

  // 重置所有筛选条件
  const resetFilters = useCallback(() => {
    setSearchQueryState('')
    setFilters({})
    setCurrentPage(1)
  }, [])

  // 筛选后的数据
  const filteredData = useMemo(() => {
    let result = [...data]

    // 应用搜索
    if (searchQuery && searchFields.length > 0) {
      const lowerQuery = searchQuery.toLowerCase().trim()
      result = result.filter(item => {
        return searchFields.some(field => {
          const value = item[field]
          if (value == null) return false
          return String(value).toLowerCase().includes(lowerQuery)
        })
      })
    }

    // 应用筛选条件
    Object.entries(filters).forEach(([key, value]) => {
      if (value !== undefined && value !== null && value !== '') {
        const filterFn = filterFunctions[key]
        if (filterFn) {
          result = result.filter(item => filterFn(item, value))
        } else {
          // 默认相等匹配
          result = result.filter(item => (item as Record<string, unknown>)[key] === value)
        }
      }
    })

    return result
  }, [data, searchQuery, searchFields, filters, filterFunctions])

  // 分页后的数据
  const paginatedData = useMemo(() => {
    const startIndex = (currentPage - 1) * pageSize
    const endIndex = startIndex + pageSize
    return filteredData.slice(startIndex, endIndex)
  }, [filteredData, currentPage, pageSize])

  // 处理分页变化
  const handlePaginationChange = useCallback((page: number, size: number) => {
    if (size !== pageSize) {
      setPageSize(size)
      setCurrentPage(1)
    } else {
      setCurrentPage(page)
    }
  }, [pageSize])

  // Ant Design Table 分页配置
  const paginationConfig: TablePaginationConfig = useMemo(() => ({
    current: currentPage,
    pageSize: pageSize,
    total: filteredData.length,
    onChange: handlePaginationChange,
    onShowSizeChange: (_, size) => handlePaginationChange(1, size),
    showSizeChanger: true,
    showQuickJumper: true,
    showTotal: (total, range) => `第 ${range[0]}-${range[1]} 条，共 ${total} 条记录`,
    pageSizeOptions,
  }), [currentPage, pageSize, filteredData.length, handlePaginationChange, pageSizeOptions])

  return {
    paginatedData,
    total: filteredData.length,
    currentPage,
    pageSize,
    searchQuery,
    filters,
    setSearchQuery,
    setFilter,
    resetFilters,
    setCurrentPage,
    setPageSize,
    paginationConfig,
    handlePaginationChange,
  }
}

export default useTableWithPagination
