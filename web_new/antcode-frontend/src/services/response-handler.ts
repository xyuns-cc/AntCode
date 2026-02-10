/**
 * API响应处理器
 * 
 * @deprecated 推荐使用 BaseService 中的方法，此类仅保留用于向后兼容。
 * 新代码请继承 BaseService 并使用其 extractData/extractPaginatedData 方法。
 */

import type { ApiResponse, PaginatedResponse, PaginationInfo } from '@/types'
import type { AxiosResponse } from 'axios'

// 后端分页响应格式
interface BackendPaginatedResponse<T> {
  success: boolean
  code: number
  message: string
  data: T[]
  pagination: PaginationInfo
  timestamp: string
}

export class ResponseHandler {
  /**
   * 提取单个数据
   */
  static extractData<T>(response: AxiosResponse<ApiResponse<T> | unknown>): T {
    const data = response.data

    // 如果是标准的 ApiResponse 格式
    if (data && typeof data === 'object') {
      // 有 data 字段且有 success 字段，返回 data
      if ('data' in data && 'success' in data) {
        return (data as ApiResponse<T>).data
      }
    }

    // 否则返回原始数据
    return data as T
  }

  /**
   * 提取分页数据
   */
  static extractPaginatedData<T>(
    response: AxiosResponse<BackendPaginatedResponse<T> | unknown>
  ): PaginatedResponse<T> {
    const data = response.data

    if (Array.isArray(data)) {
      return {
        items: data as T[],
        page: 1,
        size: data.length,
        total: data.length,
        pages: 1,
      }
    }

    // 标准分页格式: { data: [], pagination: {...} }
    if (data && typeof data === 'object') {
      const paginatedData = data as BackendPaginatedResponse<T>

      if ('pagination' in paginatedData) {
        return {
          items: paginatedData.data || [],
          page: paginatedData.pagination.page,
          size: paginatedData.pagination.size,
          total: paginatedData.pagination.total,
          pages: paginatedData.pagination.pages,
        }
      }

      // 兼容 items 字段格式
      if ('items' in paginatedData) {
        const itemsData = paginatedData as unknown as PaginatedResponse<T>
        return {
          items: itemsData.items || [],
          page: itemsData.page || 1,
          size: itemsData.size || 10,
          total: itemsData.total || 0,
          pages: itemsData.pages || 1,
        }
      }

      // 兼容其他格式
      if ('data' in paginatedData && Array.isArray(paginatedData.data)) {
        return {
          items: paginatedData.data as T[],
          page: 1,
          size: paginatedData.data.length,
          total: paginatedData.data.length,
          pages: 1,
        }
      }
    }

    // 返回空结果
    return {
      items: [],
      page: 1,
      size: 0,
      total: 0,
      pages: 0,
    }
  }

  /**
   * 检查响应是否成功
   */
  static isSuccess(response: AxiosResponse<unknown>): boolean {
    const data = response.data
    
    // HTTP状态码检查
    if (response.status >= 200 && response.status < 300) {
      // 如果有success字段，以它为准
      if (data && typeof data === 'object' && 'success' in data) {
        return Boolean((data as { success: boolean }).success)
      }
      // 否则认为成功
      return true
    }
    
    return false
  }

  /**
   * 提取错误消息
   */
  static extractErrorMessage(error: unknown): string {
    if (error && typeof error === 'object' && 'response' in error) {
      const response = (error as { response?: { data?: Record<string, unknown> } }).response
      const data = response?.data as
        | { message?: string; detail?: string | Array<{ msg?: string | undefined }> }
        | Record<string, unknown>
        | undefined
      
      // 优先返回后端提供的消息
      if (data?.message) return data.message
      if (data && 'detail' in data && data.detail) {
        // 处理detail为数组的情况（FastAPI validation errors）
        if (Array.isArray(data.detail)) {
          return data.detail
            .map((err: { msg?: string } | string) => ('msg' in err ? err.msg : err))
            .join(', ')
        }
        return String(data.detail as string)
      }
      if (data && 'error' in data && data.error) {
        return String(data.error)
      }
    }
    
    // 网络错误
    if (error && typeof error === 'object' && 'request' in error) {
      return '网络连接失败，请检查网络设置'
    }
    
    // 其他错误
    if (error instanceof Error) {
      return error.message || '请求失败'
    }

    return '请求失败'
  }
}

export default ResponseHandler
