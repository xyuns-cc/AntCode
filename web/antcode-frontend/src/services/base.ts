/**
 * Base Service 类
 * 所有业务服务的基类，提供统一的API调用和错误处理
 */

import apiClient from './api'
import type { AxiosRequestConfig, AxiosResponse } from 'axios'
import type { ApiResponse } from '@/types'
import Logger from '@/utils/logger'

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
  protected async get<T = any>(
    url: string,
    config?: AxiosRequestConfig
  ): Promise<T> {
    try {
      const fullUrl = this.getFullUrl(url)
      const response: AxiosResponse<T> = await apiClient.get(fullUrl, config)
      return this.extractData(response)
    } catch (error) {
      Logger.error(`GET ${url} failed:`, error)
      throw error
    }
  }

  /**
   * POST 请求
   */
  protected async post<T = any>(
    url: string,
    data?: any,
    config?: AxiosRequestConfig
  ): Promise<T> {
    try {
      const fullUrl = this.getFullUrl(url)
      const response: AxiosResponse<T> = await apiClient.post(fullUrl, data, config)
      return this.extractData(response)
    } catch (error) {
      Logger.error(`POST ${url} failed:`, error)
      throw error
    }
  }

  /**
   * PUT 请求
   */
  protected async put<T = any>(
    url: string,
    data?: any,
    config?: AxiosRequestConfig
  ): Promise<T> {
    try {
      const fullUrl = this.getFullUrl(url)
      const response: AxiosResponse<T> = await apiClient.put(fullUrl, data, config)
      return this.extractData(response)
    } catch (error) {
      Logger.error(`PUT ${url} failed:`, error)
      throw error
    }
  }

  /**
   * PATCH 请求
   */
  protected async patch<T = any>(
    url: string,
    data?: any,
    config?: AxiosRequestConfig
  ): Promise<T> {
    try {
      const fullUrl = this.getFullUrl(url)
      const response: AxiosResponse<T> = await apiClient.patch(fullUrl, data, config)
      return this.extractData(response)
    } catch (error) {
      Logger.error(`PATCH ${url} failed:`, error)
      throw error
    }
  }

  /**
   * DELETE 请求
   */
  protected async delete<T = any>(
    url: string,
    config?: AxiosRequestConfig
  ): Promise<T> {
    try {
      const fullUrl = this.getFullUrl(url)
      const response: AxiosResponse<T> = await apiClient.delete(fullUrl, config)
      return this.extractData(response)
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
   * 处理不同的后端响应格式
   */
  private extractData<T>(response: AxiosResponse<any>): T {
    const data = response.data

    // 如果是标准的ApiResponse格式
    if (data && typeof data === 'object') {
      // 有data字段，返回data
      if ('data' in data) {
        return data.data as T
      }
      // 有items字段（分页数据），返回整个对象
      if ('items' in data || 'pagination' in data) {
        return data as T
      }
    }

    // 否则返回原始数据
    return data as T
  }

  /**
   * 构建查询参数URL
   */
  protected buildQueryUrl(url: string, params?: Record<string, any>): string {
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
  protected async uploadFile<T = any>(
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
      return this.extractData(response)
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

