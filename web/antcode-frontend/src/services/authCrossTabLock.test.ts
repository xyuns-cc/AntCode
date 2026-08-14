import 'fake-indexeddb/auto'
import { afterEach, describe, expect, it, vi } from 'vitest'
import {
  IndexedDbAuthLeaseStore,
  type AuthLeaseStore,
  type LeaseLockOptions,
  withCrossTabLock,
  withLeaseLock,
} from './authCrossTabLock'

const TEST_LEASE_MS = 50
const TEST_RENEWAL_MS = 5
const TEST_RETRY_MS = 1
const ASYNC_SETTLE_MS = 10

const options = (now: () => number = Date.now): LeaseLockOptions => ({
  leaseDurationMs: TEST_LEASE_MS,
  renewalMs: TEST_RENEWAL_MS,
  retryMs: TEST_RETRY_MS,
  now,
})

const delay = (milliseconds: number): Promise<void> => {
  return new Promise((resolve) => setTimeout(resolve, milliseconds))
}

class RenewalFailureStore implements AuthLeaseStore {
  released = false

  async tryAcquire(): Promise<boolean> {
    return true
  }

  async renew(): Promise<boolean> {
    return false
  }

  async release(): Promise<void> {
    this.released = true
  }
}

describe('cross-tab authentication lock', () => {
  afterEach(() => vi.unstubAllGlobals())

  it('prefers the native Web Locks API', async () => {
    const request = vi.fn(async (_name: string, action: () => Promise<string>) => action())
    vi.stubGlobal('navigator', { locks: { request } })

    await expect(withCrossTabLock('native-lock', async () => 'done')).resolves.toBe('done')
    expect(request).toHaveBeenCalledWith('native-lock', expect.any(Function))
  })

  it('uses the IndexedDB lease when Web Locks are unavailable', async () => {
    vi.stubGlobal('navigator', {})

    await expect(withCrossTabLock('indexed-db-lock', async () => 'done')).resolves.toBe('done')
  })

  it('serializes concurrent IndexedDB lease holders', async () => {
    vi.stubGlobal('navigator', {})
    const order: string[] = []
    let releaseFirst = (): void => {
      throw new Error('first action was not initialized')
    }
    const firstCanFinish = new Promise<void>((resolve) => {
      releaseFirst = resolve
    })
    const first = withCrossTabLock('serialized-lock', async () => {
      order.push('first:start')
      await firstCanFinish
      order.push('first:end')
    })
    await delay(ASYNC_SETTLE_MS)
    const second = withCrossTabLock('serialized-lock', async () => {
      order.push('second:start')
    })

    await delay(ASYNC_SETTLE_MS)
    expect(order).toEqual(['first:start'])
    releaseFirst()
    await Promise.all([first, second])
    expect(order).toEqual(['first:start', 'first:end', 'second:start'])
  })

  it('releases the lease when the action fails', async () => {
    vi.stubGlobal('navigator', {})
    await expect(
      withCrossTabLock('failed-action-lock', async () => {
        throw new Error('action failed')
      })
    ).rejects.toThrow('action failed')

    await expect(withCrossTabLock('failed-action-lock', async () => 'recovered')).resolves.toBe(
      'recovered'
    )
  })

  it('recovers an expired lease atomically', async () => {
    const store = new IndexedDbAuthLeaseStore()
    let now = 0
    await store.tryAcquire({
      name: 'expired-lock',
      owner: 'crashed-owner',
      now,
      expiresAt: TEST_LEASE_MS,
    })
    now = TEST_LEASE_MS + 1

    await expect(
      withLeaseLock({
        name: 'expired-lock',
        action: async () => 'recovered',
        store,
        options: options(() => now),
      })
    ).resolves.toBe('recovered')
  })

  it('does not let a different owner renew or release a lease', async () => {
    const store = new IndexedDbAuthLeaseStore()
    await store.tryAcquire({
      name: 'owned-lock',
      owner: 'owner-a',
      now: 0,
      expiresAt: TEST_LEASE_MS,
    })

    const foreignOwner = { name: 'owned-lock', owner: 'owner-b' }
    await expect(store.renew({ ...foreignOwner, expiresAt: TEST_LEASE_MS * 2 })).resolves.toBe(
      false
    )
    await store.release(foreignOwner)
    const contender = { name: 'owned-lock', owner: 'owner-c', now: 0, expiresAt: TEST_LEASE_MS }
    await expect(store.tryAcquire(contender)).resolves.toBe(false)
  })

  it('surfaces renewal ownership loss and still releases', async () => {
    const store = new RenewalFailureStore()
    await expect(
      withLeaseLock({
        name: 'renewal-lock',
        action: async () => {
          await delay(TEST_RENEWAL_MS * 2)
          return 'unsafe-result'
        },
        store,
        options: options(),
      })
    ).rejects.toThrow('所有权已丢失')
    expect(store.released).toBe(true)
  })

  it('fails explicitly when IndexedDB is unavailable', async () => {
    vi.stubGlobal('indexedDB', undefined)
    const store = new IndexedDbAuthLeaseStore()

    const request = { name: 'missing-db', owner: 'owner', now: 0, expiresAt: TEST_LEASE_MS }
    await expect(store.tryAcquire(request)).rejects.toThrow('不支持 IndexedDB')
  })
})
