import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'

import type { Project } from '@/types'
import ProjectRuntimeBinding from './ProjectRuntimeBinding'

/**
 * 集群加到第二个 Worker 后曾出现「项目 100% 跑不了」，而项目详情 / 列表 / 编辑抽屉
 * 里都没有任何地方显示环境在哪个节点，用户完全无法自查。这里守住那块可见性。
 */
const project = (overrides: Partial<Project>): Project =>
  ({
    id: 'p1',
    name: 'proj',
    type: 'file',
    env_location: 'worker',
    worker_env_name: 'shared-py311',
    python_version: '3.11.11',
    ...overrides,
  }) as Project

describe('ProjectRuntimeBinding', () => {
  it('names the worker that actually holds the runtime', () => {
    render(<ProjectRuntimeBinding project={project({ bound_worker_name: 'worker-ui-001' })} />)

    expect(screen.getByText('worker-ui-001')).toBeInTheDocument()
    expect(screen.getByText(/shared-py311/)).toBeInTheDocument()
  })

  it('calls out an unbound project instead of showing a blank field', () => {
    render(<ProjectRuntimeBinding project={project({ bound_worker_name: undefined, worker_id: undefined })} />)

    expect(screen.getByText('未绑定')).toBeInTheDocument()
    expect(screen.getByText(/很可能因缺少运行时环境而失败/)).toBeInTheDocument()
  })

  it('stays out of the way for projects with no worker runtime', () => {
    const { container } = render(
      <ProjectRuntimeBinding project={project({ env_location: undefined, worker_env_name: undefined })} />
    )

    expect(container).toBeEmptyDOMElement()
  })
})
