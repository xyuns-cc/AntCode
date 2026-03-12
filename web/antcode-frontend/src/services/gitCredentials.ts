import { BaseService } from './base'
import type {
  GitCredential,
  GitCredentialCreateRequest,
  GitCredentialUpdateRequest,
} from '@/types'

class GitCredentialService extends BaseService {
  constructor() {
    super('/api/v1/git-credentials')
  }

  async listGitCredentials(): Promise<GitCredential[]> {
    return this.get<GitCredential[]>('')
  }

  async getGitCredential(id: string): Promise<GitCredential> {
    return this.get<GitCredential>(`/${id}`)
  }

  async createGitCredential(payload: GitCredentialCreateRequest): Promise<GitCredential> {
    return this.post<GitCredential>('', payload)
  }

  async updateGitCredential(id: string, payload: GitCredentialUpdateRequest): Promise<GitCredential> {
    return this.put<GitCredential>(`/${id}`, payload)
  }

  async deleteGitCredential(id: string): Promise<void> {
    await this.delete(`/${id}`)
  }
}

export const gitCredentialService = new GitCredentialService()
export default gitCredentialService

