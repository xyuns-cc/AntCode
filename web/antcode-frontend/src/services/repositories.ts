import { BaseService } from './base'
import type {
  GitRepository,
  ImportProjectsPayload,
  ImportProjectsResult,
  RepositoryCreatePayload,
  RepositoryScanResult,
  RepositoryUpdatePayload,
} from '@/types/repository'

class RepositoryService extends BaseService {
  constructor() {
    super('/api/v1/repositories')
  }

  list() {
    return this.get<GitRepository[]>('')
  }

  create(payload: RepositoryCreatePayload) {
    return this.post<GitRepository>('', payload)
  }

  update(repositoryId: string, payload: RepositoryUpdatePayload) {
    return this.put<GitRepository>(`/${repositoryId}`, payload)
  }

  remove(repositoryId: string) {
    return this.delete<null>(`/${repositoryId}`)
  }

  // ref 只作用于这一次扫描，服务端不会写回 default_ref
  // （repository_service.scan_for_user 只把它传给 _scan_repository）。
  // 改默认分支是 update() 的事，两个意图不共用一个入口。
  scan(repositoryId: string, ref?: string) {
    return this.post<RepositoryScanResult>(`/${repositoryId}/scan`, { ref })
  }
}

class RepositoryProjectImportService extends BaseService {
  // Round6 P1-FE (5.4): 之前基址错为 /api/v1/projects, 但后端实际路径是
  // /api/v1/repositories/import-from-repository (repositories.py:98),
  // 拼出的完整 URL /api/v1/projects/import-from-repository 会 404。
  constructor() {
    super('/api/v1/repositories')
  }

  importFromRepository(payload: ImportProjectsPayload) {
    return this.post<ImportProjectsResult>('/import-from-repository', payload)
  }
}

export const repositoryService = new RepositoryService()
export const repositoryProjectImportService = new RepositoryProjectImportService()
