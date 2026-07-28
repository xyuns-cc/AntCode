import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'
import type { User } from '@/types'
import { UserActions } from './UserActions'

const makeUser = (id: string, role: User['role']): User => ({
  id,
  username: id,
  is_active: true,
  is_admin: role !== 'user',
  role,
  created_at: '2026-07-13T00:00:00Z',
  updated_at: '2026-07-13T00:00:00Z',
})

describe('UserActions', () => {
  it('confirms and invokes the real kick action for a super administrator', async () => {
    const user = userEvent.setup()
    const kick = vi.fn().mockResolvedValue(undefined)
    const actions = { edit: vi.fn(), resetPassword: vi.fn(), kick, delete: vi.fn() }
    render(<UserActions currentUser={makeUser('root', 'super_admin')} target={makeUser('alice', 'user')} actions={actions} />)

    await user.click(screen.getByRole('button', { name: /踢下线/ }))
    await user.click(await screen.findByRole('button', { name: /确\s*定/ }))

    expect(kick).toHaveBeenCalledOnce()
  })

  it('does not expose privileged actions to a normal administrator', () => {
    const actions = { edit: vi.fn(), resetPassword: vi.fn(), kick: vi.fn(), delete: vi.fn() }
    render(<UserActions currentUser={makeUser('admin', 'admin')} target={makeUser('alice', 'user')} actions={actions} />)

    expect(screen.queryByRole('button', { name: /踢下线/ })).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /改密/ })).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /删除/ })).not.toBeInTheDocument()
  })
})
