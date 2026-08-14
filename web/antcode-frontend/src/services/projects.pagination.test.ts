import { beforeEach, describe, expect, it, vi } from 'vitest'

import apiClient from './api'
import { projectService } from './projects'

vi.mock('./api', () => ({
  default: { get: vi.fn() },
  unwrapResponse: vi.fn(),
}))

const response = (
  items: Array<{ id: string }>,
  pagination: { page: number; size: number; total: number }
) => ({
  data: {
    data: {
      items,
      pagination: {
        ...pagination,
        pages: Math.ceil(pagination.total / pagination.size),
      },
    },
  },
})

const projectItems = (count: number) =>
  Array.from({ length: count }, (_, index) => ({ id: `p-${index + 1}` }))

describe('projectService.getAllProjects', () => {
  beforeEach(() => vi.clearAllMocks())

  it('reads every server page without truncating project options', async () => {
    vi.mocked(apiClient.get)
      .mockResolvedValueOnce(response(projectItems(500), { page: 1, size: 500, total: 501 }))
      .mockResolvedValueOnce(response([{ id: 'p-501' }], { page: 2, size: 500, total: 501 }))

    const projects = await projectService.getAllProjects({ worker_id: 'worker-1' })

    expect(projects).toHaveLength(501)
    expect(projects.at(-1)?.id).toBe('p-501')
    expect(apiClient.get).toHaveBeenNthCalledWith(1, '/api/v1/projects', {
      params: { worker_id: 'worker-1', page: 1, size: 500 },
    })
    expect(apiClient.get).toHaveBeenNthCalledWith(2, '/api/v1/projects', {
      params: { worker_id: 'worker-1', page: 2, size: 500 },
    })
  })

  it('fails explicitly when pagination ends before the declared total', async () => {
    vi.mocked(apiClient.get)
      .mockResolvedValueOnce(response(projectItems(500), { page: 1, size: 500, total: 501 }))
      .mockResolvedValueOnce(response([], { page: 2, size: 500, total: 501 }))

    await expect(projectService.getAllProjects()).rejects.toThrow('项目分页响应提前结束')
  })

  it('rejects mismatched page metadata instead of looping on duplicate data', async () => {
    vi.mocked(apiClient.get)
      .mockResolvedValueOnce(response(projectItems(500), { page: 1, size: 500, total: 501 }))
      .mockResolvedValueOnce(response([{ id: 'p-501' }], { page: 1, size: 500, total: 501 }))

    await expect(projectService.getAllProjects()).rejects.toThrow('项目分页页码不一致')
  })

  it('rejects duplicate project ids across otherwise valid pages', async () => {
    vi.mocked(apiClient.get)
      .mockResolvedValueOnce(response(projectItems(500), { page: 1, size: 500, total: 501 }))
      .mockResolvedValueOnce(response([{ id: 'p-1' }], { page: 2, size: 500, total: 501 }))

    await expect(projectService.getAllProjects()).rejects.toThrow('项目分页包含重复项目')
  })
})
