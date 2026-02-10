/**
 * Base Service 类
 * 所有业务服务的基类，提供统一的API调用和错误处理
 */

import apiClient from './api'
import type { AxiosRequestConfig, AxiosResponse } from 'axios'
import type { PaginatedResponse, PaginationInfo } from '@/types'
import Logger from '@/utils/logger'

// 后端响应格式
interface BackendResponse<T> {
  success: boolean
  code: number
  message: string
  data: T
  timestamp: string
}

// 后端分页响应格式
interface BackendPaginatedResponse<T> {
  success: boolean
  code: number
  message: string
  data: T[]
  pagination: PaginationInfo
  timestamp: string
}

export class BaseService {
  /**
   * 基础路径前缀
   */
  protected basePath: string

  constructor(basePath: string) {
    this.basePath = basePath
  }

  /**
   * GET 请求
   */
  protected async get<T = unknown>(
    url: string,
    config?: AxiosRequestConfig
  ): Promise<T> {
    try {
      const fullUrl = this.getFullUrl(url)
      const response: AxiosResponse = await apiClient.get(fullUrl, config)
      return this.extractData<T>(response)
    } catch (error) {
      Logger.error(`GET ${url} failed:`, error)
      throw error
    }
  }

  /**
   * GET 分页请求
   */
  protected async getPaginated<T>(
    url: string,
    config?: AxiosRequestConfig
  ): Promise<PaginatedResponse<T>> {
    try {
      const fullUrl = this.getFullUrl(url)
      const response: AxiosResponse<BackendPaginatedResponse<T>> = await apiClient.get(fullUrl, config)
      return this.extractPaginatedData<T>(response)
    } catch (error) {
      Logger.error(`GET ${url} failed:`, error)
      throw error
    }
  }

  /**
   * POST 请求
   */
  protected async post<T = unknown>(
    url: string,
    data?: unknown,
    config?: AxiosRequestConfig
  ): Promise<T> {
    try {
      const fullUrl = this.getFullUrl(url)
      const response: AxiosResponse = await apiClient.post(fullUrl, data, config)
      return this.extractData<T>(response)
    } catch (error) {
      Logger.error(`POST ${url} failed:`, error)
      throw error
    }
  }

  /**
   * PUT 请求
   */
  protected async put<T = unknown>(
    url: string,
    data?: unknown,
    config?: AxiosRequestConfig
  ): Promise<T> {
    try {
      const fullUrl = this.getFullUrl(url)
      const response: AxiosResponse = await apiClient.put(fullUrl, data, config)
      return this.extractData<T>(response)
    } catch (error) {
      Logger.error(`PUT ${url} failed:`, error)
      throw error
    }
  }

  /**
   * PATCH 请求
   */
  protected async patch<T = unknown>(
    url: string,
    data?: unknown,
    config?: AxiosRequestConfig
  ): Promise<T> {
    try {
      const fullUrl = this.getFullUrl(url)
      const response: AxiosResponse = await apiClient.patch(fullUrl, data, config)
      return this.extractData<T>(response)
    } catch (error) {
      Logger.error(`PATCH ${url} failed:`, error)
      throw error
    }
  }

  /**
   * DELETE 请求
   */
  protected async delete<T = unknown>(
    url: string,
    config?: AxiosRequestConfig
  ): Promise<T> {
    try {
      const fullUrl = this.getFullUrl(url)
      const response: AxiosResponse = await apiClient.delete(fullUrl, config)
      return this.extractData<T>(response)
    } catch (error) {
      Logger.error(`DELETE ${url} failed:`, error)
      throw error
    }
  }

  /**
   * 获取完整URL
   */
  private getFullUrl(url: string): string {
    // 如果url已经是完整路径（以/api开头），直接返回
    if (url.startsWith('/api')) {
      return url
    }
    // 如果url以/开头，拼接basePath
    if (url.startsWith('/')) {
      return `${this.basePath}${url}`
    }
    // 否则在中间加上/
    return `${this.basePath}/${url}`
  }

  /**
   * 提取响应数据
   * 处理后端 BaseResponse 格式: { success, code, message, data, timestamp }
   */
  private extractData<T>(response: AxiosResponse<BackendResponse<T> | T>): T {
    const data = response.data

    // 如果是标准的 BaseResponse 格式
    if (data && typeof data === 'object' && 'data' in data && 'success' in data) {
      return (data as BackendResponse<T>).data
    }

    // 否则返回原始数据
    return data as T
  }

  /**
   * 提取分页响应数据
   * 处理后端 PaginationResponse 格式: { success, code, message, data: [], pagination: {...}, timestamp }
   */
  private extractPaginatedData<T>(response: AxiosResponse<BackendPaginatedResponse<T>>): PaginatedResponse<T> {
    const data = response.data

    // 标准分页格式
    if (data && typeof data === 'object' && 'pagination' in data) {
      return {
        items: data.data || [],
        page: data.pagination.page,
        size: data.pagination.size,
        total: data.pagination.total,
        pages: data.pagination.pages,
      }
    }

    // 兼容直接返回数组的情况
    if (Array.isArray(data)) {
      return {
        items: data as T[],
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
   * 构建查询参数URL
   */
  protected buildQueryUrl(
    url: string,
    params?: Record<string, string | number | boolean | Array<string | number | boolean>>
  ): string {
    if (!params || Object.keys(params).length === 0) {
      return url
    }

    const queryString = Object.entries(params)
      .filter(([_, value]) => value !== undefined && value !== null && value !== '')
      .map(([key, value]) => `${encodeURIComponent(key)}=${encodeURIComponent(String(value))}`)
      .join('&')

    return queryString ? `${url}?${queryString}` : url
  }

  /**
   * 上传文件
   */
  protected async uploadFile<T = unknown>(
    url: string,
    formData: FormData,
    onProgress?: (progress: number) => void
  ): Promise<T> {
    try {
      const fullUrl = this.getFullUrl(url)
      const response = await apiClient.post(fullUrl, formData, {
        headers: {
          'Content-Type': 'multipart/form-data',
        },
        onUploadProgress: (progressEvent) => {
          if (onProgress && progressEvent.total) {
            const progress = Math.round((progressEvent.loaded * 100) / progressEvent.total)
            onProgress(progress)
          }
        },
      })
      return this.extractData<T>(response)
    } catch (error) {
      Logger.error(`Upload ${url} failed:`, error)
      throw error
    }
  }

  /**
   * 下载文件
   */
  protected async downloadFile(
    url: string,
    filename?: string
  ): Promise<void> {
    try {
      const fullUrl = this.getFullUrl(url)
      const response = await apiClient.get(fullUrl, {
        responseType: 'blob',
      })

      // 创建下载链接
      const blob = new Blob([response.data])
      const downloadUrl = window.URL.createObjectURL(blob)
      const link = document.createElement('a')
      link.href = downloadUrl

      // 设置文件名
      if (filename) {
        link.download = filename
      } else {
        // 从响应头获取文件名
        const contentDisposition = response.headers['content-disposition']
        if (contentDisposition) {
          const matches = /filename[^;=\n]*=((['"]).*?\2|[^;\n]*)/.exec(contentDisposition)
          if (matches && matches[1]) {
            link.download = matches[1].replace(/['"]/g, '')
          }
        }
      }

      // 触发下载
      document.body.appendChild(link)
      link.click()
      document.body.removeChild(link)
      window.URL.revokeObjectURL(downloadUrl)
    } catch (error) {
      Logger.error(`Download ${url} failed:`, error)
      throw error
    }
  }
}

export default BaseService
