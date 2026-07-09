/**
 * 执行(TaskRun)相关 API：产物 list / download。日志走 logs.ts。
 */
import { BaseService } from './base'
import apiClient from './api'

export interface RunArtifact {
  name: string
  artifact_type: string
  size_bytes: number
  checksum: string
  mime_type: string
  created_at?: string | null
}

export interface RunArtifactsResponse {
  items: RunArtifact[]
}

export interface SpiderItem {
  _id: string
  run_id?: string
  project_id?: string
  spider_name?: string
  data?: Record<string, unknown> | string
  [key: string]: unknown
}

export interface SpiderItemsResponse {
  items: SpiderItem[]
  last_id: string
  count: number
}

class RunsService extends BaseService {
  constructor() {
    super('/api/v1/runs')
  }

  /** 列出某次执行的所有产物（不含下载 URI）。 */
  async listArtifacts(runId: string): Promise<RunArtifact[]> {
    const response = await this.get<RunArtifactsResponse>(`/${runId}/artifacts`)
    return response.items || []
  }

  /** O2: 列出该 run 由 SpiderDataReporter 写入的抓取数据条目。 */
  async listSpiderItems(
    runId: string,
    params?: { startId?: string; count?: number },
  ): Promise<SpiderItemsResponse> {
    const query: Record<string, string | number> = {}
    if (params?.startId) query.start_id = params.startId
    if (params?.count) query.count = params.count
    return this.get<SpiderItemsResponse>(`/${runId}/spider-items`, { params: query })
  }

  /**
   * 触发浏览器下载单个产物。走原生 blob 流以保留 Content-Disposition。
   * 用 axios 直接下 blob，避免 BaseService 的 JSON envelope 拦截。
   */
  async downloadArtifact(runId: string, artifactName: string): Promise<void> {
    const url = `/api/v1/runs/${encodeURIComponent(runId)}/artifacts/${encodeURIComponent(
      artifactName,
    )}/download`
    const response = await apiClient.get(url, { responseType: 'blob' })
    const blob = response.data as Blob

    const objectUrl = URL.createObjectURL(blob)
    try {
      const link = document.createElement('a')
      link.href = objectUrl
      link.download = artifactName
      document.body.appendChild(link)
      link.click()
      document.body.removeChild(link)
    } finally {
      // 延迟释放，避免 click 未生效前被回收
      setTimeout(() => URL.revokeObjectURL(objectUrl), 4000)
    }
  }
}

export const runsService = new RunsService()
