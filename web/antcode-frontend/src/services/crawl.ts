/**
 * Crawl 批次 API 客户端 (R1-P2-28)
 *
 * 后端 18 个批次/指标端点齐全但此前前端 0 引用 —— 用户完全无入口。
 * 这里补上 crawl service 让 UI 页面能查/建/暂停/恢复/取消批次 +
 * 聚合抓取数据 + CSV/JSON 导出。
 */
import { BaseService } from './base'

export interface CrawlBatchSummary {
  // 后端 CrawlBatchResponse.id 承载的是 batch.public_id（对齐 crawl.py:110）；
  // 前端别再自造 batch_id 名字，历史上因此走出 /batches/undefined/... 请求。
  id: string
  project_id: string
  name: string
  description?: string
  status: 'pending' | 'running' | 'paused' | 'completed' | 'failed' | 'cancelled'
  is_test?: boolean
  seed_urls?: string[]
  max_depth?: number
  max_pages?: number
  max_concurrency?: number
  request_delay?: number
  timeout?: number
  created_at: string
  started_at?: string
  completed_at?: string
}

export interface CrawlBatchListResult {
  items: CrawlBatchSummary[]
  total: number
  page: number
  size: number
  pages: number
}

export interface CrawlBatchCreatePayload {
  project_id: string
  name: string
  seed_urls: string[]
  description?: string
  max_depth?: number
  max_pages?: number
  max_concurrency?: number
  request_delay?: number
  timeout?: number
  max_retries?: number
}

export interface CrawlBatchProgress {
  batch_id: string
  total_urls: number
  pending_urls: number
  completed_urls: number
  failed_urls: number
  progress_percentage?: number
}

export interface CrawlBatchItem {
  sequence: number
  url: string
  timestamp: string
  run_id: string
  data: Record<string, unknown>
}

class CrawlService extends BaseService {
  constructor() {
    // batches/metrics 端点挂在 /api/v1/crawl 前缀下
    super('/api/v1/crawl')
  }

  // 批次列表（支持项目筛选）。后端返回 PaginationResponse ——
  // BaseService.extractData 会剥掉最外层 `.data`，拿到的是
  // `{ items, pagination: { page, size, total, pages } }`（见
  // packages/antcode_core/.../schemas/common.py PaginationData）。
  // 前端上层只关心 items+total，这里把嵌套拍平，避免 BatchList 里出现
  // res.total ?? list.length 之类的 fallback（总数会永远等于当前页长度）。
  async listBatches(params?: {
    project_id?: string
    status?: string
    page?: number
    size?: number
  }): Promise<CrawlBatchListResult> {
    const raw = await this.get<{
      items: CrawlBatchSummary[]
      pagination: { page: number; size: number; total: number; pages: number }
    }>('/batches', { params })
    const items = raw?.items ?? []
    const pagination = raw?.pagination
    return {
      items,
      total: pagination?.total ?? items.length,
      page: pagination?.page ?? params?.page ?? 1,
      size: pagination?.size ?? params?.size ?? items.length,
      pages: pagination?.pages ?? 1,
    }
  }

  // 批次详情
  async getBatch(batchId: string): Promise<CrawlBatchSummary> {
    return this.get<CrawlBatchSummary>(`/batches/${batchId}`)
  }

  // 创建批次
  async createBatch(payload: CrawlBatchCreatePayload): Promise<CrawlBatchSummary> {
    return this.post<CrawlBatchSummary>('/batches', payload)
  }

  // 启动
  async startBatch(batchId: string): Promise<CrawlBatchSummary> {
    return this.post<CrawlBatchSummary>(`/batches/${batchId}/start`, {})
  }

  // 暂停
  async pauseBatch(batchId: string): Promise<CrawlBatchSummary> {
    return this.post<CrawlBatchSummary>(`/batches/${batchId}/pause`, {})
  }

  // 恢复
  async resumeBatch(batchId: string): Promise<CrawlBatchSummary> {
    return this.post<CrawlBatchSummary>(`/batches/${batchId}/resume`, {})
  }

  // 取消
  async cancelBatch(batchId: string): Promise<CrawlBatchSummary> {
    return this.post<CrawlBatchSummary>(`/batches/${batchId}/cancel`, {})
  }

  // 进度
  async getProgress(batchId: string): Promise<CrawlBatchProgress> {
    return this.get<CrawlBatchProgress>(`/batches/${batchId}/progress`)
  }

  // 批次维度聚合数据（R1-P2-28）
  async getBatchItems(
    batchId: string,
    limit = 100
  ): Promise<{ batch_id: string; items: CrawlBatchItem[]; count: number }> {
    return this.get<{ batch_id: string; items: CrawlBatchItem[]; count: number }>(
      `/batches/${batchId}/items`,
      { params: { limit } }
    )
  }

  // 导出：走浏览器下载（历史 URL 拼接方式）
  //
  // P2-15: 保留只为兼容旧调用点，新代码请用 exportBatch()。
  // window.open(exportBatchUrl(...)) 会绕过 axios 拦截器，Authorization
  // 头不会带上 → 服务端 401。
  exportBatchUrl(batchId: string, format: 'json' | 'csv'): string {
    return `/api/v1/crawl/batches/${batchId}/export?format=${format}`
  }

  /**
   * P2-15: 通过 axios 走 Bearer 认证下载导出文件，再本地触发下载。
   *
   * 原来 BatchList 直接 `window.open(exportBatchUrl(...))`，浏览器新标签页
   * 不会带 axios 拦截器注入的 Authorization 头，登录用户导出永远 401。
   * 这里改成 responseType: 'blob' 走鉴权拿字节流，再合成 <a download>
   * 触发下载。
   */
  async exportBatch(batchId: string, format: 'json' | 'csv'): Promise<void> {
    // BaseService.downloadFile 已经封装了 blob 请求 + content-disposition
    // 解析 + <a> 触发；这里直接复用，避免各页面再手写一遍下载胶水。
    await this.downloadFile(
      `/batches/${batchId}/export?format=${format}`,
      `crawl-batch-${batchId}.${format}`,
      { timeout: 0 }
    )
  }
}

export const crawlService = new CrawlService()
