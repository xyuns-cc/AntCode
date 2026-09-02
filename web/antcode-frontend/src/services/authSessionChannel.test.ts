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

// username 与 session_jti 分开给：账号过滤那一支只有在两者不同步时才起作用，
// 用 `session-${username}` 这种绑死的构造喂不到它。
const tokenFor = (username: string, sessionJti: string): string => {
  const payload = btoa(
    JSON.stringify({
      exp: Math.floor(Date.now() / 1000) + 3600,
      username,
      token_type: 'access',
      session_jti: sessionJti,
    })
  )
  return `header.${payload}.signature`
}

const futureToken = (username = 'alice'): string => tokenFor(username, `session-${username}`)

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

/** 一个「对端标签页」：收到 token_request 后按顺序回若干个候选令牌。 */
const respondWith = (tokens: string[]): void => {
  const peer = new FakeBroadcastChannel('antcode-auth-session')
  peer.addEventListener('message', (event) => {
    const request = event.data as { type: string; requestId: string }
    if (request.type !== 'token_request') return
    for (const token of tokens) {
      peer.postMessage({ type: 'token_response', requestId: request.requestId, token })
    }
  })
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

  /**
   * 这三条替换掉原来那一条「ignores peer tokens belonging to a different account」。
   *
   * 原判据是 `expect(token).not.toBeNull()` + `expect(getAccessToken()).toBe(token)`：后者恒真
   * ——实现永远把自己选中的那个装进内存——而前者对「收下了别人的令牌」同样成立，于是那条用例
   * 从来没有验过它标题里写的那件事。实测把 tokenMatchesRequest 整个换成 `return true`，
   * 全仓 426 条用例一条不红。
   *
   * 现在钉的是「装进内存的到底是哪一个」，并把 session_jti 与 username 两道过滤分开钉：
   * 原来的 fixture 两个候选令牌连 session_jti 都不同，账号那一支根本没被走到。
   */
  it('对端先回别的账号、再回本账号时，收下的是本账号那一个', async () => {
    setSessionGeneration('session-new-account')
    const foreign = futureToken('old-account')
    const mine = futureToken('new-account')
    respondWith([foreign, mine])

    const token = await requestPeerAccessToken({
      sessionJti: 'session-new-account',
      username: 'new-account',
    })

    expect(token).toBe(mine)
    expect(getAccessToken()).toBe(mine)
  })

  it('同一会话标识、同一账号的令牌照常收下', async () => {
    setSessionGeneration('session-shared')
    const mine = tokenFor('alice', 'session-shared')
    respondWith([mine])

    await expect(
      requestPeerAccessToken({ sessionJti: 'session-shared', username: 'alice' })
    ).resolves.toBe(mine)
    expect(getAccessToken()).toBe(mine)
  })

  it('同一账号但旧会话标识的令牌不收 —— 那是上一次登录留下的', async () => {
    // 单独钉 session_jti 那一支：上面两条里的候选令牌账号也不同，account 分支先把它挡了，
    // jti 分支实际没被走到（实测单独删掉它，那两条照样绿）。
    setSessionGeneration('session-current')
    respondWith([tokenFor('alice', 'session-previous')])

    await expect(
      requestPeerAccessToken({ sessionJti: 'session-current', username: 'alice' })
    ).resolves.toBeNull()
    expect(getAccessToken()).toBeNull()
  })

  it('会话标识对得上但账号对不上的令牌一概不收', async () => {
    // 正是 tokenMatchesRequest 里 username 那一支存在的理由：只按 session_jti 过滤挡不住它。
    setSessionGeneration('session-shared')
    respondWith([tokenFor('bob', 'session-shared')])

    await expect(
      requestPeerAccessToken({ sessionJti: 'session-shared', username: 'alice' })
    ).resolves.toBeNull()
    expect(getAccessToken()).toBeNull()
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
