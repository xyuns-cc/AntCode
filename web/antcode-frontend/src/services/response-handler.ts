/**
 * API响应处理器
 * 提供统一的响应数据提取和错误处理
 */

import type { ApiResponse, PaginatedResponse } from '@/types'
import type { AxiosResponse } from 'axios'

export class ResponseHandler {
  /**
   * 提取单个数据
   */
  static extractData<T>(response: AxiosResponse<ApiResponse<T> | any>): T {
    const data = response.data

    // 如果是标准的ApiResponse格式
    if (data && typeof data === 'object') {
      // 有data字段，返回data
      if ('data' in data) {
        return data.data as T
      }
    }

    // 否则返回原始数据
    return data as T
  }

  /**
   * 提取分页数据
   */
  static extractPaginatedData<T>(
    response: AxiosResponse<any>
  ): PaginatedResponse<T> {
    const data = response.data

    // 标准分页格式
    if (data && typeof data === 'object') {
      if ('items' in data || 'pagination' in data) {
        return {
          items: data.items || data.data || [],
          page: data.page || data.pagination?.page || 1,
          size: data.size || data.pagination?.size || 10,
          total: data.total || data.pagination?.total || 0,
          pages: data.pages || data.pagination?.pages || 1,
        }
      }

      // 兼容其他格式
      if ('data' in data && Array.isArray(data.data)) {
        return {
          items: data.data,
          page: 1,
          size: data.data.length,
          total: data.data.length,
          pages: 1,
        }
      }
    }

    // 如果data直接是数组
    if (Array.isArray(data)) {
      return {
        items: data,
        page: 1,
        size: data.length,
        total: data.length,
        pages: 1,
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
  static isSuccess(response: AxiosResponse<any>): boolean {
    const data = response.data
    
    // HTTP状态码检查
    if (response.status >= 200 && response.status < 300) {
      // 如果有success字段，以它为准
      if (data && typeof data === 'object' && 'success' in data) {
        return Boolean(data.success)
      }
      // 否则认为成功
      return true
    }
    
    return false
  }

  /**
   * 提取错误消息
   */
  static extractErrorMessage(error: any): string {
    if (error.response) {
      const { data } = error.response
      
      // 优先返回后端提供的消息
      if (data?.message) return data.message
      if (data?.detail) {
        // 处理detail为数组的情况（FastAPI validation errors）
        if (Array.isArray(data.detail)) {
          return data.detail
            .map((err: any) => err.msg || err)
            .join(', ')
        }
        return String(data.detail)
      }
      if (data?.error) return data.error
    }
    
    // 网络错误
    if (error.request) {
      return '网络连接失败，请检查网络设置'
    }
    
    // 其他错误
    return error.message || '请求失败'
  }
}

export default ResponseHandler

