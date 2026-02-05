import type React from 'react'

// 导出所有类型定义
export * from './api'
export * from './project'
export * from './task'
export * from './node'
export * from './system-config'

// 通用类型定义
export interface SelectOption {
  label: string
  value: string | number
  disabled?: boolean
  children?: SelectOption[]
}

// 表格列配置
export interface TableColumn<T = Record<string, unknown>> {
  key: string
  title: string
  dataIndex?: string
  width?: number | string
  align?: 'left' | 'center' | 'right'
  fixed?: 'left' | 'right'
  sortable?: boolean
  filterable?: boolean
  render?: (value: unknown, record: T, index: number) => React.ReactNode
}

// 分页配置
export interface PaginationConfig {
  current: number
  pageSize: number
  total: number
  showSizeChanger?: boolean
  showQuickJumper?: boolean
  showTotal?: (total: number, range: [number, number]) => string
  pageSizeOptions?: string[]
}

// 表单字段配置
export interface FormField {
  name: string
  label: string
  type: 'input' | 'textarea' | 'select' | 'checkbox' | 'radio' | 'date' | 'upload' | 'custom'
  required?: boolean
  placeholder?: string
  options?: SelectOption[]
  rules?: Array<{
    required?: boolean
    message?: string
    pattern?: RegExp
    min?: number
    max?: number
    validator?: (rule: unknown, value: unknown) => Promise<void>
  }>
  disabled?: boolean
  hidden?: boolean
  span?: number
  render?: () => React.ReactNode
}

// 搜索配置
export interface SearchConfig {
  placeholder?: string
  allowClear?: boolean
  onSearch?: (value: string) => void
  onChange?: (value: string) => void
}

// 过滤器配置
export interface FilterConfig {
  key: string
  label: string
  type: 'select' | 'date' | 'dateRange' | 'input'
  options?: SelectOption[]
  placeholder?: string
  defaultValue?: unknown
}

// 操作按钮配置
export interface ActionButton {
  key: string
  label: string
  type?: 'primary' | 'default' | 'dashed' | 'text' | 'link'
  danger?: boolean
  icon?: React.ReactNode
  disabled?: boolean
  loading?: boolean
  onClick?: () => void
  confirm?: {
    title: string
    content?: string
  }
}

// 状态标签配置
export interface StatusTag {
  status: string
  color: string
  text: string
}

// 菜单项配置
export interface MenuItem {
  key: string
  label: string
  icon?: React.ReactNode
  path?: string
  children?: MenuItem[]
  disabled?: boolean
  hidden?: boolean
}

// 面包屑配置
export interface BreadcrumbItem {
  title: string
  path?: string
  icon?: React.ReactNode
}

// 通知配置
export interface NotificationConfig {
  type: 'success' | 'info' | 'warning' | 'error'
  message: string
  description?: string
  duration?: number
  placement?: 'topLeft' | 'topRight' | 'bottomLeft' | 'bottomRight'
}

// 模态框配置
export interface ModalConfig {
  title: string
  content: React.ReactNode
  width?: number | string
  centered?: boolean
  maskClosable?: boolean
  keyboard?: boolean
  footer?: React.ReactNode | null
  onOk?: () => void | Promise<void>
  onCancel?: () => void
}

// 抽屉配置
export interface DrawerConfig {
  title: string
  content: React.ReactNode
  width?: number | string
  placement?: 'left' | 'right' | 'top' | 'bottom'
  maskClosable?: boolean
  keyboard?: boolean
  footer?: React.ReactNode
  onClose?: () => void
}

// 图表数据点
export interface ChartDataPoint {
  x: string | number
  y: number
  category?: string
  label?: string
}

// 图表配置
export interface ChartConfig {
  type: 'line' | 'bar' | 'pie' | 'area' | 'scatter'
  data: ChartDataPoint[]
  xAxis?: {
    title?: string
    type?: 'category' | 'time' | 'value'
  }
  yAxis?: {
    title?: string
    min?: number
    max?: number
  }
  legend?: boolean
  tooltip?: boolean
  colors?: string[]
}

// 文件上传配置
export interface UploadConfig {
  accept?: string
  multiple?: boolean
  maxSize?: number
  maxCount?: number
  directory?: boolean
  beforeUpload?: (file: File) => boolean | Promise<boolean>
  onProgress?: (percent: number) => void
  onSuccess?: (response: unknown, file: File) => void
  onError?: (error: Error, file: File) => void
}

// 键值对
export interface KeyValuePair {
  key: string
  value: string
}

// 时间范围
export interface TimeRange {
  start: Date
  end: Date
}

// 排序配置
export interface SortConfig {
  field: string
  order: 'asc' | 'desc'
}

// 主题配置
export interface ThemeConfig {
  primaryColor: string
  borderRadius: number
  fontSize: number
  colorBgBase: string
  colorTextBase: string
}

// 用户偏好设置
export interface UserPreferences {
  theme: 'light' | 'dark' | 'auto'
  language: string
  timezone: string
  dateFormat: string
  pageSize: number
  autoRefresh: boolean
  refreshInterval: number
  notifications: {
    desktop: boolean
    email: boolean
    sound: boolean
  }
}
