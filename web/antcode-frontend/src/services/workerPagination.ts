import type { Worker, WorkerListResponse } from '@/types'

const WORKER_LIST_PAGE_SIZE = 100

type WorkerPageLoader = (page: number, size: number) => Promise<WorkerListResponse>

export async function collectAllWorkers(loadPage: WorkerPageLoader): Promise<Worker[]> {
  const workers: Worker[] = []
  let page = 1
  while (true) {
    const result = await loadPage(page, WORKER_LIST_PAGE_SIZE)
    const items = result.items ?? []
    workers.push(...items)
    if (workers.length >= result.total) return workers
    if (items.length === 0) {
      throw new Error(`Worker 分页响应提前结束: received=${workers.length}, total=${result.total}`)
    }
    page += 1
  }
}
