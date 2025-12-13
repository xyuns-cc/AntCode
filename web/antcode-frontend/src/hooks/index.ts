/**
 * Hooks 统一导出
 */

// API 相关
export { useApi, usePaginatedApi, useFormApi, useUploadApi } from './useApi'
export type { default as useApiDefault } from './useApi'

// 认证
export { useAuth } from './useAuth'

// 客户端筛选
export {
  useClientSideFilter,
  usePaginatedClientFilter,
  useSortableClientFilter,
} from './useClientSideFilter'
export type {
  FilterOptions,
  UseClientSideFilterResult,
  UsePaginatedClientFilterResult,
  UseSortableClientFilterResult,
  SortConfig,
} from './useClientSideFilter'

// 表格分页
export { useTableWithPagination } from './useTableWithPagination'
export type {
  TablePaginationConfig,
  UseTableWithPaginationOptions,
  UseTableWithPaginationResult,
} from './useTableWithPagination'

// 消息
export { useMessage } from './useMessage'

// 主题
export { useTheme } from './useTheme'
