import { useState, useMemo, useCallback } from 'react'

/**
 * 客户端筛选Hook
 * 用于在前端进行数据筛选，减少不必要的后端请求
 */

export interface FilterOptions<T> {
  searchFields?: (keyof T)[]  // 需要搜索的字段
  filterFunctions?: {
    [key: string]: (item: T, value: any) => boolean
  }
}

export interface UseClientSideFilterResult<T> {
  filteredData: T[]
  searchQuery: string
  setSearchQuery: (query: string) => void
  filters: Record<string, any>
  setFilter: (key: string, value: any) => void
  resetFilters: () => void
  applyFilters: () => void
}

/**
 * 通用前端筛选Hook
 * @param data 原始数据
 * @param options 筛选选项
 */
export function useClientSideFilter<T>(
  data: T[],
  options: FilterOptions<T> = {}
): UseClientSideFilterResult<T> {
  const { searchFields = [], filterFunctions = {} } = options

  const [searchQuery, setSearchQuery] = useState('')
  const [filters, setFilters] = useState<Record<string, any>>({})

  // 设置单个筛选器
  const setFilter = useCallback((key: string, value: any) => {
    setFilters(prev => {
      if (value === undefined || value === null || value === '') {
        const { [key]: _, ...rest } = prev
        return rest
      }
      return { ...prev, [key]: value }
    })
  }, [])

  // 重置所有筛选器
  const resetFilters = useCallback(() => {
    setSearchQuery('')
    setFilters({})
  }, [])

  // 应用筛选器（手动触发）
  const applyFilters = useCallback(() => {
    // 这个方法主要用于强制重新计算
    // 由于使用了useMemo，依赖项变化会自动重新计算
  }, [])

  // 筛选后的数据
  const filteredData = useMemo(() => {
    let result = [...data]

    // 应用搜索查询
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

    // 应用自定义筛选函数
    Object.entries(filters).forEach(([key, value]) => {
      if (value !== undefined && value !== null && value !== '') {
        const filterFn = filterFunctions[key]
        if (filterFn) {
          result = result.filter(item => filterFn(item, value))
        } else {
          // 默认相等匹配
          result = result.filter(item => (item as any)[key] === value)
        }
      }
    })

    return result
  }, [data, searchQuery, searchFields, filters, filterFunctions])

  return {
    filteredData,
    searchQuery,
    setSearchQuery,
    filters,
    setFilter,
    resetFilters,
    applyFilters
  }
}

/**
 * 带分页的客户端筛选Hook
 */
export interface UsePaginatedClientFilterResult<T> extends UseClientSideFilterResult<T> {
  paginatedData: T[]
  currentPage: number
  pageSize: number
  totalPages: number
  totalItems: number
  setCurrentPage: (page: number) => void
  setPageSize: (size: number) => void
}

export function usePaginatedClientFilter<T>(
  data: T[],
  options: FilterOptions<T> = {},
  initialPage = 1,
  initialPageSize = 10
): UsePaginatedClientFilterResult<T> {
  const [currentPage, setCurrentPage] = useState(initialPage)
  const [pageSize, setPageSize] = useState(initialPageSize)

  const filterResult = useClientSideFilter(data, options)
  const { filteredData } = filterResult

  // 计算分页数据
  const paginatedData = useMemo(() => {
    const startIndex = (currentPage - 1) * pageSize
    const endIndex = startIndex + pageSize
    return filteredData.slice(startIndex, endIndex)
  }, [filteredData, currentPage, pageSize])

  const totalPages = Math.ceil(filteredData.length / pageSize)
  const totalItems = filteredData.length

  // 当筛选条件变化时，重置到第一页
  const setSearchQuery = useCallback((query: string) => {
    filterResult.setSearchQuery(query)
    setCurrentPage(1)
  }, [filterResult])

  const setFilter = useCallback((key: string, value: any) => {
    filterResult.setFilter(key, value)
    setCurrentPage(1)
  }, [filterResult])

  return {
    ...filterResult,
    setSearchQuery,
    setFilter,
    paginatedData,
    currentPage,
    pageSize,
    totalPages,
    totalItems,
    setCurrentPage,
    setPageSize
  }
}

/**
 * 带排序的客户端筛选Hook
 */
export interface SortConfig<T> {
  key: keyof T
  direction: 'asc' | 'desc'
}

export interface UseSortableClientFilterResult<T> extends UseClientSideFilterResult<T> {
  sortedData: T[]
  sortConfig: SortConfig<T> | null
  setSortConfig: (config: SortConfig<T> | null) => void
  toggleSort: (key: keyof T) => void
}

export function useSortableClientFilter<T>(
  data: T[],
  options: FilterOptions<T> = {}
): UseSortableClientFilterResult<T> {
  const [sortConfig, setSortConfig] = useState<SortConfig<T> | null>(null)
  const filterResult = useClientSideFilter(data, options)
  const { filteredData } = filterResult

  // 排序后的数据
  const sortedData = useMemo(() => {
    if (!sortConfig) return filteredData

    const sorted = [...filteredData]
    sorted.sort((a, b) => {
      const aValue = a[sortConfig.key]
      const bValue = b[sortConfig.key]

      if (aValue == null && bValue == null) return 0
      if (aValue == null) return 1
      if (bValue == null) return -1

      let comparison = 0
      if (typeof aValue === 'string' && typeof bValue === 'string') {
        comparison = aValue.localeCompare(bValue)
      } else if (typeof aValue === 'number' && typeof bValue === 'number') {
        comparison = aValue - bValue
      } else {
        comparison = String(aValue).localeCompare(String(bValue))
      }

      return sortConfig.direction === 'asc' ? comparison : -comparison
    })

    return sorted
  }, [filteredData, sortConfig])

  // 切换排序
  const toggleSort = useCallback((key: keyof T) => {
    setSortConfig(prev => {
      if (!prev || prev.key !== key) {
        return { key, direction: 'asc' }
      }
      if (prev.direction === 'asc') {
        return { key, direction: 'desc' }
      }
      return null
    })
  }, [])

  return {
    ...filterResult,
    sortedData,
    sortConfig,
    setSortConfig,
    toggleSort
  }
}

