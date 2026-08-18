/**
 * 项目删除是跨 6 张表 + Redis 的级联操作，确认框只说"此操作不可恢复"信息量不够。
 * 这些用例钉住"点确认前用户能看到会失去什么"。
 */
import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import type { Project } from '@/types'
import ProjectDeleteWarning from './ProjectDeleteWarning'

const project = {
  id: 'proj-1',
  name: 'uiw4-rule-pub',
  type: 'rule',
  status: 'active',
  created_at: '2026-08-18T00:00:00Z',
  updated_at: '2026-08-18T00:00:00Z',
  task_count: 3
} as unknown as Project

describe('ProjectDeleteWarning', () => {
  it('列出会被一并删除的下游数据，而不只是"不可恢复"', () => {
    render(<ProjectDeleteWarning project={project} />)

    expect(screen.getByText(/uiw4-rule-pub/)).toBeInTheDocument()
    expect(screen.getByText(/全部任务及其调度/)).toBeInTheDocument()
    expect(screen.getByText(/执行记录与执行日志/)).toBeInTheDocument()
    expect(screen.getByText(/爬取批次/)).toBeInTheDocument()
    expect(screen.getByText(/运行时绑定与源码快照/)).toBeInTheDocument()
  })

  it('回显该项目当前的任务数', () => {
    const { container } = render(<ProjectDeleteWarning project={project} />)

    expect(container.textContent).toContain('3')
    expect(container.textContent).toContain('个任务')
  })

  it('没有选中项目时什么都不渲染', () => {
    const { container } = render(<ProjectDeleteWarning project={null} />)

    expect(container).toBeEmptyDOMElement()
  })
})
