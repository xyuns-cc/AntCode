import { BaseService } from './base'
import type {
  GitRepository,
  ImportProjectsPayload,
  ImportProjectsResult,
  RepositoryCreatePayload,
  RepositoryScanResult,
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

  scan(repositoryId: string, ref?: string) {
    return this.post<RepositoryScanResult>(`/${repositoryId}/scan`, { ref })
  }
}

class RepositoryProjectImportService extends BaseService {
  constructor() {
    super('/api/v1/projects')
  }

  importFromRepository(payload: ImportProjectsPayload) {
    return this.post<ImportProjectsResult>('/import-from-repository', payload)
  }
}

export const repositoryService = new RepositoryService()
export const repositoryProjectImportService = new RepositoryProjectImportService()
