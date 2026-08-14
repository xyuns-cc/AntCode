import 'fake-indexeddb/auto'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import {
  clearAccessToken,
  clearSessionGeneration,
  getAccessToken,
  setSessionGeneration,
} from './authToken'
import {
  publishAccessToken,
  requestPeerAccessToken,
  resetAuthSessionChannelForTests,
  subscribeAccessTokenUpdates,
  withCrossTabRefreshLock,
} from './authSessionChannel'

const futureToken = (username = 'alice'): string => {
  const payload = btoa(
    JSON.stringify({
      exp: Math.floor(Date.now() / 1000) + 3600,
      username,
      token_type: 'access',
      session_jti: `session-${username}`,
    })
  )
  return `header.${payload}.signature`
}

class FakeBroadcastChannel {
  static instances: FakeBroadcastChannel[] = []
  readonly name: string
  private listeners = new Set<(event: MessageEvent) => void>()

  constructor(name: string) {
    this.name = name
    FakeBroadcastChannel.instances.push(this)
  }

  addEventListener(_type: string, listener: (event: MessageEvent) => void): void {
    this.listeners.add(listener)
  }

  postMessage(message: unknown): void {
    for (const peer of FakeBroadcastChannel.instances) {
      if (peer !== this && peer.name === this.name) peer.emit(message)
    }
  }

  close(): void {
    FakeBroadcastChannel.instances = FakeBroadcastChannel.instances.filter((item) => item !== this)
  }

  private emit(data: unknown): void {
    for (const listener of this.listeners) listener({ data } as MessageEvent)
  }
}

describe('auth session channel', () => {
  beforeEach(() => {
    vi.stubGlobal('BroadcastChannel', FakeBroadcastChannel)
    FakeBroadcastChannel.instances = []
    clearAccessToken()
  })

  afterEach(() => {
    resetAuthSessionChannelForTests()
    clearSessionGeneration()
    vi.unstubAllGlobals()
  })

  it('obtains the current in-memory token from another same-origin tab', async () => {
    const token = futureToken()
    setSessionGeneration('session-alice')
    const peer = new FakeBroadcastChannel('antcode-auth-session')
    peer.addEventListener('message', (event) => {
      const request = event.data as { type: string; requestId: string }
      if (request.type === 'token_request') {
        peer.postMessage({ type: 'token_response', requestId: request.requestId, token })
      }
    })

    await expect(requestPeerAccessToken({ sessionJti: 'session-alice' })).resolves.toBe(token)
    expect(getAccessToken()).toBe(token)
  })

  it('pushes a newly rotated token to an already-open tab', () => {
    const token = futureToken()
    setSessionGeneration('session-alice')
    const peer = new FakeBroadcastChannel('antcode-auth-session')
    const received: unknown[] = []
    peer.addEventListener('message', (event) => received.push(event.data))

    publishAccessToken(token)

    expect(received).toContainEqual({
      type: 'token_update',
      token,
      sessionJti: 'session-alice',
    })
  })

  it('notifies the current tab before it can keep using stale account state', () => {
    const listener = vi.fn()
    const unsubscribe = subscribeAccessTokenUpdates(listener)
    setSessionGeneration('session-replacement')
    requestPeerAccessToken({ sessionJti: 'session-replacement' })
    const peer = new FakeBroadcastChannel('antcode-auth-session')
    const token = futureToken('replacement')

    peer.postMessage({ type: 'token_update', token, sessionJti: 'session-replacement' })

    expect(getAccessToken()).toBe(token)
    expect(listener).toHaveBeenCalledWith(token)
    unsubscribe()
  })

  it('ignores peer tokens belonging to a different account', async () => {
    setSessionGeneration('session-new-account')
    const peer = new FakeBroadcastChannel('antcode-auth-session')
    peer.addEventListener('message', (event) => {
      const request = event.data as { type: string; requestId: string }
      if (request.type === 'token_request') {
        peer.postMessage({
          type: 'token_response',
          requestId: request.requestId,
          token: futureToken('old-account'),
        })
        peer.postMessage({
          type: 'token_response',
          requestId: request.requestId,
          token: futureToken('new-account'),
        })
      }
    })

    const token = await requestPeerAccessToken({
      sessionJti: 'session-new-account',
      username: 'new-account',
    })

    expect(token).not.toBeNull()
    expect(getAccessToken()).toBe(token)
  })

  it('rejects a delayed token update after the shared generation is cleared', () => {
    const listener = vi.fn()
    subscribeAccessTokenUpdates(listener)
    setSessionGeneration('session-alice')
    requestPeerAccessToken({ sessionJti: 'session-alice' })
    const peer = new FakeBroadcastChannel('antcode-auth-session')
    clearSessionGeneration()

    peer.postMessage({
      type: 'token_update',
      token: futureToken('alice'),
      sessionJti: 'session-alice',
    })

    expect(getAccessToken()).toBeNull()
    expect(listener).not.toHaveBeenCalled()
  })

  it('serializes refresh through the browser-wide Web Lock', async () => {
    const request = vi.fn(async (_name: string, action: () => Promise<string>) => action())
    vi.stubGlobal('navigator', { locks: { request } })

    await expect(withCrossTabRefreshLock(async () => 'done')).resolves.toBe('done')
    expect(request).toHaveBeenCalledWith('antcode-auth-refresh', expect.any(Function))
  })

  it('creates peer request IDs without secure-context randomUUID', async () => {
    vi.stubGlobal('crypto', {
      getRandomValues: (values: Uint32Array) => {
        values.fill(7)
        return values
      },
    })
    setSessionGeneration('session-alice')
    const peer = new FakeBroadcastChannel('antcode-auth-session')
    const requestIds: string[] = []
    peer.addEventListener('message', (event) => {
      const request = event.data as { type: string; requestId: string }
      if (request.type === 'token_request') requestIds.push(request.requestId)
    })

    await requestPeerAccessToken({ sessionJti: 'session-alice' })

    expect(requestIds).toEqual(['00000007000000070000000700000007'])
  })

  it('fails explicitly when BroadcastChannel is unavailable', async () => {
    vi.stubGlobal('BroadcastChannel', undefined)

    await expect(withCrossTabRefreshLock(async () => 'unsafe')).rejects.toThrow(
      '无法安全同步多标签会话'
    )
  })

  it('uses the IndexedDB lock when native Web Locks are unavailable', async () => {
    vi.stubGlobal('navigator', {})

    await expect(withCrossTabRefreshLock(async () => 'safe')).resolves.toBe('safe')
  })
})
