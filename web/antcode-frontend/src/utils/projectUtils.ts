/**
 * 项目相关的工具函数
 */

import type { ProjectType, ProjectStatus } from '@/types'

// 项目类型中文映射
export const PROJECT_TYPE_MAP: Record<ProjectType, string> = {
  file: '文件项目',
  rule: '规则项目',
  code: '代码项目'
}

// 项目状态中文映射
export const PROJECT_STATUS_MAP: Record<string, string> = {
  active: '活跃',
  inactive: '非活跃',
  error: '错误',
  draft: '草稿',
  published: '已发布',
  archived: '已归档'
}

// 项目类型颜色映射
export const PROJECT_TYPE_COLORS: Record<ProjectType, string> = {
  file: 'blue',
  rule: 'green', 
  code: 'orange'
}

// 项目状态颜色映射
export const PROJECT_STATUS_COLORS: Record<string, string> = {
  active: 'success',
  inactive: 'default',
  error: 'error',
  draft: 'processing',
  published: 'success',
  archived: 'warning'
}

/**
 * 获取项目类型的中文显示名称
 * @param type 项目类型
 * @returns 中文名称
 */
export const getProjectTypeText = (type: ProjectType): string => {
  return PROJECT_TYPE_MAP[type] || type
}

/**
 * 获取项目状态的中文显示名称
 * @param status 项目状态
 * @returns 中文名称
 */
export const getProjectStatusText = (status: string): string => {
  return PROJECT_STATUS_MAP[status] || status
}

/**
 * 获取项目类型的标签颜色
 * @param type 项目类型
 * @returns 颜色值
 */
export const getProjectTypeColor = (type: ProjectType): string => {
  return PROJECT_TYPE_COLORS[type] || 'default'
}

/**
 * 获取项目状态的标签颜色
 * @param status 项目状态
 * @returns 颜色值
 */
export const getProjectStatusColor = (status: string): string => {
  return PROJECT_STATUS_COLORS[status] || 'default'
}

/**
 * 获取项目类型图标
 * @param type 项目类型
 * @returns 图标名称或组件
 */
export const getProjectTypeIcon = (type: ProjectType): string => {
  const iconMap: Record<ProjectType, string> = {
    file: 'FileOutlined',
    rule: 'SettingOutlined',
    code: 'CodeOutlined'
  }
  return iconMap[type] || 'FileOutlined'
}

/**
 * 获取项目类型的描述
 * @param type 项目类型
 * @returns 描述文本
 */
export const getProjectTypeDescription = (type: ProjectType): string => {
  const descriptionMap: Record<ProjectType, string> = {
    file: '上传完整的项目文件或压缩包',
    rule: '配置网页数据采集规则',
    code: '直接编写或上传源代码'
  }
  return descriptionMap[type] || ''
}

/**
 * 获取项目状态的描述
 * @param status 项目状态
 * @returns 描述文本
 */
export const getProjectStatusDescription = (status: string): string => {
  const descriptionMap: Record<string, string> = {
    active: '项目正常运行中',
    inactive: '项目暂停或未激活',
    error: '项目运行出现错误',
    draft: '项目处于草稿状态',
    published: '项目已发布可用',
    archived: '项目已归档'
  }
  return descriptionMap[status] || ''
}
