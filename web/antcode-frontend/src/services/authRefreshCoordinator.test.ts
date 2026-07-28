import { beforeEach, describe, expect, it, vi } from 'vitest'

const mocks = vi.hoisted(() => ({
  decodeAccessToken: vi.fn(),
  getAccessToken: vi.fn<() => string | null>(),
  getSessionAccount: vi.fn<() => string | null>(),
  getSessionGeneration: vi.fn<() => string | null>(),
  publishAccessToken: vi.fn(),
  requestPeerAccessToken: vi.fn<() => Promise<string | null>>(),
  setAccessToken: vi.fn(),
  setSessionGeneration: vi.fn(),
  setSessionHint: vi.fn(),
  withCrossTabRefreshLock: vi.fn(async <T>(action: () => Promise<T>) => action()),
}))

vi.mock('./authToken', () => ({
  decodeAccessToken: mocks.decodeAccessToken,
  getAccessToken: mocks.getAccessToken,
  getSessionAccount: mocks.getSessionAccount,
  getSessionGeneration: mocks.getSessionGeneration,
  setAccessToken: mocks.setAccessToken,
  setSessionGeneration: mocks.setSessionGeneration,
  setSessionHint: mocks.setSessionHint,
}))
vi.mock('./authSessionChannel', () => ({
  publishAccessToken: mocks.publishAccessToken,
  requestPeerAccessToken: mocks.requestPeerAccessToken,
  withCrossTabRefreshLock: mocks.withCrossTabRefreshLock,
}))

import {
  AuthAccountChangedError,
  coordinateSessionRefresh,
  replacementForStaleToken,
} from './authRefreshCoordinator'

describe('cross-tab refresh coordination', () => {
  beforeEach(() => {
    mocks.decodeAccessToken.mockReturnValue({
      username: 'alice',
      session_jti: 'session-current',
    })
    mocks.getAccessToken.mockReturnValue('old-token')
    mocks.getSessionAccount.mockReturnValue('alice')
    mocks.getSessionGeneration.mockReturnValue('session-current')
    mocks.requestPeerAccessToken.mockResolvedValue(null)
  })

  it('reuses a token refreshed by another tab while waiting for the global lock', async () => {
    mocks.getAccessToken
      .mockReturnValueOnce('old-token')
      .mockReturnValueOnce('new-token')
    const requestRefresh = vi.fn()

    await expect(coordinateSessionRefresh(requestRefresh)).resolves.toEqual({
      access_token: 'new-token',
      token_type: 'bearer',
    })
    expect(requestRefresh).not.toHaveBeenCalled()
  })

  it('uses a peer token instead of rotating the shared refresh cookie again', async () => {
    mocks.requestPeerAccessToken.mockResolvedValue('peer-token')
    const requestRefresh = vi.fn()

    await expect(coordinateSessionRefresh(requestRefresh)).resolves.toEqual({
      access_token: 'peer-token',
      token_type: 'bearer',
    })
    expect(requestRefresh).not.toHaveBeenCalled()
    expect(mocks.requestPeerAccessToken).toHaveBeenCalledWith({
      sessionJti: 'session-current',
      username: 'alice',
    })
  })

  it('publishes the one backend refresh result to every tab', async () => {
    const payload = { access_token: 'backend-token', token_type: 'bearer' }
    const requestRefresh = vi.fn().mockResolvedValue(payload)

    await expect(coordinateSessionRefresh(requestRefresh)).resolves.toBe(payload)
    expect(mocks.setAccessToken).toHaveBeenCalledWith('backend-token')
    expect(mocks.setSessionGeneration).toHaveBeenCalledWith('session-current', 'alice')
    expect(mocks.setSessionHint).toHaveBeenCalledOnce()
    expect(mocks.publishAccessToken).toHaveBeenCalledWith('backend-token')
  })

  it('finds a replacement for an in-flight request sent with a revoked token', async () => {
    mocks.getAccessToken.mockReturnValue('new-token')

    await expect(replacementForStaleToken('old-token')).resolves.toEqual({
      kind: 'replace',
      token: 'new-token',
    })
    expect(mocks.requestPeerAccessToken).not.toHaveBeenCalled()
  })

  it('only requests stale-token replacements from the same account', async () => {
    mocks.getAccessToken.mockReturnValue('old-token')
    mocks.requestPeerAccessToken.mockResolvedValue('same-user-new-token')

    await expect(replacementForStaleToken('old-token')).resolves.toEqual({
      kind: 'replace',
      token: 'same-user-new-token',
    })
    expect(mocks.requestPeerAccessToken).toHaveBeenCalledWith({
      sessionJti: 'session-current',
      username: 'alice',
    })
  })

  it('cancels a refresh when the active token belongs to another account', async () => {
    mocks.getAccessToken
      .mockReturnValueOnce('old-token')
      .mockReturnValueOnce('other-account-token')
    mocks.decodeAccessToken.mockImplementation((token: string) => ({
      username: token === 'other-account-token' ? 'bob' : 'alice',
      session_jti: 'session-current',
    }))
    const requestRefresh = vi.fn()

    await expect(coordinateSessionRefresh(requestRefresh)).rejects.toBeInstanceOf(
      AuthAccountChangedError,
    )
    expect(requestRefresh).not.toHaveBeenCalled()
  })

  it('does not replace an old request with another account token', async () => {
    mocks.getAccessToken.mockReturnValue('other-account-token')
    mocks.decodeAccessToken.mockImplementation((token: string) => ({
      username: token === 'other-account-token' ? 'bob' : 'alice',
      session_jti: 'session-current',
    }))

    await expect(replacementForStaleToken('old-token')).resolves.toEqual({
      kind: 'account_changed',
    })
    expect(mocks.requestPeerAccessToken).not.toHaveBeenCalled()
  })

  it('does not query old-account peers after the shared account changes', async () => {
    mocks.getSessionAccount.mockReturnValue('bob')

    await expect(replacementForStaleToken('old-token')).resolves.toEqual({
      kind: 'account_changed',
    })
    expect(mocks.requestPeerAccessToken).not.toHaveBeenCalled()
  })

  it('does not rotate the shared cookie after another account becomes current', async () => {
    mocks.getSessionAccount.mockReturnValue('bob')
    const requestRefresh = vi.fn()

    await expect(coordinateSessionRefresh(requestRefresh)).rejects.toBeInstanceOf(
      AuthAccountChangedError,
    )
    expect(mocks.requestPeerAccessToken).not.toHaveBeenCalled()
    expect(requestRefresh).not.toHaveBeenCalled()
  })
})
