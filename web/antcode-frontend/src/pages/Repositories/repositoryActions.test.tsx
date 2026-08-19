import { beforeEach, describe, expect, it, vi } from 'vitest'
import { App } from 'antd'
import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import type { GitRepository } from '@/types/repository'
import Repositories from './index'

const repository: GitRepository = {
  id: 'repo-1',
  name: '走查仓库',
  url: 'https://example.test/crawler.git',
  default_ref: 'main',
  credential_id: null,
  enabled: true,
  created_at: '2026-08-19T00:00:00Z',
  updated_at: '2026-08-19T00:00:00Z',
}

const list = vi.fn()
const update = vi.fn()
const remove = vi.fn()
const scan = vi.fn()

vi.mock('@/services/repositories', () => ({
  repositoryService: {
    list: (...args: unknown[]) => list(...args),
    create: vi.fn(),
    update: (...args: unknown[]) => update(...args),
    remove: (...args: unknown[]) => remove(...args),
    scan: (...args: unknown[]) => scan(...args),
  },
  repositoryProjectImportService: { importFromRepository: vi.fn() },
}))

vi.mock('@/services/gitCredentials', () => ({
  gitCredentialService: { listGitCredentials: vi.fn().mockResolvedValue([]) },
}))

vi.mock('@/services/workers', () => ({
  workerService: { getMyAvailableWorkers: vi.fn().mockResolvedValue([]) },
}))

const renderPage = async () => {
  render(
    <App>
      <Repositories />
    </App>
  )
  await screen.findByText('走查仓库')
}

const clickAction = async (label: string) => {
  const user = userEvent.setup()
  await user.click(screen.getByRole('button', { name: new RegExp(label) }))
  return user
}

// antd 会往两个汉字的按钮文案中间插一个空格（"删 除"），按名字找控件时必须容忍。
const confirmDelete = async (user: ReturnType<typeof userEvent.setup>) => {
  const popup = await screen.findByRole('tooltip')
  await user.click(within(popup).getByRole('button', { name: /删\s*除/ }))
}

beforeEach(() => {
  list.mockResolvedValue([repository])
  update.mockResolvedValue(repository)
  remove.mockResolvedValue(null)
  scan.mockResolvedValue({ repository_id: repository.id, ref: 'main', candidates: [] })
})

// 缺陷：操作列此前只有「扫描导入」，URL 填错的仓库在 UI 上既改不了也删不掉。
// 下面每条都从真实渲染的按钮点起，不直接调 handler，也不 spy 任何确认框实现。
describe('repository row actions', () => {
  it('persists a new default ref through the edit drawer', async () => {
    await renderPage()
    const user = await clickAction('编辑')

    const drawer = await screen.findByRole('dialog')
    const refInput = within(drawer).getByLabelText('默认引用')
    expect(refInput).toHaveValue('main')
    await user.clear(refInput)
    await user.type(refInput, 'develop')
    await user.click(within(drawer).getByRole('button', { name: /保\s*存/ }))

    await waitFor(() => expect(update).toHaveBeenCalledTimes(1))
    expect(update).toHaveBeenCalledWith('repo-1', {
      name: '走查仓库',
      url: 'https://example.test/crawler.git',
      default_ref: 'develop',
      credential_id: null,
    })
  })

  it('deletes the repository once the confirmation popup is accepted', async () => {
    await renderPage()
    const user = await clickAction('删除')

    // 确认框必须是生产代码真的渲染出来的 DOM；点它之前 remove 不许被调用。
    await screen.findByRole('tooltip')
    expect(remove).not.toHaveBeenCalled()
    await confirmDelete(user)

    await waitFor(() => expect(remove).toHaveBeenCalledWith('repo-1'))
  })

  it('does not report success when the server refuses an in-use repository', async () => {
    // 后端对仍被 ProjectSource 引用的仓库回 409（repository_service.delete_for_user）。
    remove.mockRejectedValue(Object.assign(new Error('Git 仓库仍被项目引用'), {
      response: { status: 409, data: { message: 'Git 仓库仍被项目引用' } },
    }))
    await renderPage()
    expect(list).toHaveBeenCalledTimes(1)
    const user = await clickAction('删除')

    await confirmDelete(user)

    await waitFor(() => expect(remove).toHaveBeenCalledWith('repo-1'))
    expect(screen.queryByText(/已删除仓库/)).toBeNull()
    // 服务端拒绝后必须重新拉列表：本地这份已经不能代表服务端状态了。
    await waitFor(() => expect(list).toHaveBeenCalledTimes(2))
    expect(screen.getByText('走查仓库')).toBeTruthy()
  })
})

// 缺陷：扫描抽屉没有 ref 输入框，永远用建仓时定死的 default_ref。
// 「本次扫描用别的分支」和「改掉默认分支」是两个意图，必须是两个控件。
describe('scan drawer ref override', () => {
  it('scans a non-default ref without persisting it as the new default', async () => {
    await renderPage()
    const user = await clickAction('扫描导入')

    const refInput = await screen.findByLabelText('本次扫描引用')
    expect(refInput).toHaveValue('main')
    await user.clear(refInput)
    await user.type(refInput, 'feature/spiders')
    await user.click(screen.getByRole('button', { name: /扫描仓库/ }))

    await waitFor(() => expect(scan).toHaveBeenCalledWith('repo-1', 'feature/spiders'))
    expect(update).not.toHaveBeenCalled()
  })

  it('falls back to the stored default ref when the field is left untouched', async () => {
    await renderPage()
    const user = await clickAction('扫描导入')

    await screen.findByLabelText('本次扫描引用')
    await user.click(screen.getByRole('button', { name: /扫描仓库/ }))

    await waitFor(() => expect(scan).toHaveBeenCalledWith('repo-1', 'main'))
  })
})
