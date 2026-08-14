const LOCK_DATABASE_NAME = 'antcode-auth-coordination'
const LOCK_DATABASE_VERSION = 1
const LOCK_STORE_NAME = 'leases'
const LEASE_DURATION_MS = 60_000
const LEASE_RENEWAL_MS = 10_000
const LEASE_RETRY_MS = 50
const OWNER_ID_WORDS = 4

type LeaseRecord = {
  name: string
  owner: string
  expiresAt: number
}

type LeaseMutation = (current: LeaseRecord | undefined) => LeaseRecord | null | undefined

export interface AuthLeaseStore {
  tryAcquire(request: LeaseAcquisition): Promise<boolean>
  renew(request: LeaseOwnership): Promise<boolean>
  release(request: LeaseIdentity): Promise<void>
}

export type LeaseIdentity = Readonly<{ name: string; owner: string }>
export type LeaseOwnership = Readonly<LeaseIdentity & { expiresAt: number }>
export type LeaseAcquisition = Readonly<LeaseOwnership & { now: number }>

export type LeaseLockOptions = Readonly<{
  leaseDurationMs: number
  renewalMs: number
  retryMs: number
  now: () => number
}>

export type LeaseLockRequest<T> = Readonly<{
  name: string
  action: () => Promise<T>
  store: AuthLeaseStore
  options?: LeaseLockOptions
}>

type LeaseContext = Readonly<{
  name: string
  owner: string
  store: AuthLeaseStore
  options: LeaseLockOptions
}>

const DEFAULT_OPTIONS: LeaseLockOptions = Object.freeze({
  leaseDurationMs: LEASE_DURATION_MS,
  renewalMs: LEASE_RENEWAL_MS,
  retryMs: LEASE_RETRY_MS,
  now: Date.now,
})

const transactionError = (transaction: IDBTransaction): Error => {
  return transaction.error ?? new Error('IndexedDB 认证锁事务失败')
}

export class IndexedDbAuthLeaseStore implements AuthLeaseStore {
  private databasePromise: Promise<IDBDatabase> | null = null

  async tryAcquire(request: LeaseAcquisition): Promise<boolean> {
    const { name, owner, now, expiresAt } = request
    return this.mutate(name, (current) => {
      if (current && current.expiresAt > now) return undefined
      return { name, owner, expiresAt }
    })
  }

  async renew(request: LeaseOwnership): Promise<boolean> {
    const { name, owner, expiresAt } = request
    return this.mutate(name, (current) => {
      if (!current || current.owner !== owner) return undefined
      return { name, owner, expiresAt }
    })
  }

  async release(request: LeaseIdentity): Promise<void> {
    const { name, owner } = request
    await this.mutate(name, (current) => {
      return current?.owner === owner ? null : undefined
    })
  }

  private getDatabase(): Promise<IDBDatabase> {
    if (!globalThis.indexedDB) {
      return Promise.reject(new Error('当前浏览器不支持 IndexedDB，无法安全协调多标签会话'))
    }
    this.databasePromise ??= this.openDatabase().catch((error: unknown) => {
      this.databasePromise = null
      throw error
    })
    return this.databasePromise
  }

  private openDatabase(): Promise<IDBDatabase> {
    return new Promise((resolve, reject) => {
      const request = globalThis.indexedDB.open(LOCK_DATABASE_NAME, LOCK_DATABASE_VERSION)
      request.onupgradeneeded = () => {
        if (!request.result.objectStoreNames.contains(LOCK_STORE_NAME)) {
          request.result.createObjectStore(LOCK_STORE_NAME, { keyPath: 'name' })
        }
      }
      request.onsuccess = () => {
        const database = request.result
        database.onversionchange = () => {
          database.close()
          this.databasePromise = null
        }
        resolve(database)
      }
      request.onerror = () => reject(request.error ?? new Error('无法打开 IndexedDB 认证锁数据库'))
      request.onblocked = () => reject(new Error('IndexedDB 认证锁数据库升级被阻塞'))
    })
  }

  private async mutate(name: string, mutation: LeaseMutation): Promise<boolean> {
    const database = await this.getDatabase()
    return new Promise((resolve, reject) => {
      const transaction = database.transaction(LOCK_STORE_NAME, 'readwrite')
      const store = transaction.objectStore(LOCK_STORE_NAME)
      const request = store.get(name)
      let changed = false
      request.onsuccess = () => {
        const next = mutation(request.result as LeaseRecord | undefined)
        if (next === undefined) return
        changed = true
        if (next === null) store.delete(name)
        else store.put(next)
      }
      request.onerror = () => transaction.abort()
      transaction.oncomplete = () => resolve(changed)
      transaction.onerror = () => reject(transactionError(transaction))
      transaction.onabort = () => reject(transactionError(transaction))
    })
  }
}

type HeartbeatState = { failure: Error | null }

const createOwnerId = (): string => {
  const values = new Uint32Array(OWNER_ID_WORDS)
  crypto.getRandomValues(values)
  return Array.from(values, (value) => value.toString(16).padStart(8, '0')).join('')
}

const waitForDelay = (milliseconds: number, signal: AbortSignal): Promise<void> => {
  return new Promise((resolve) => {
    const timer = setTimeout(resolve, milliseconds)
    signal.addEventListener(
      'abort',
      () => {
        clearTimeout(timer)
        resolve()
      },
      { once: true }
    )
  })
}

const acquireLease = async (context: LeaseContext): Promise<void> => {
  const { store, name, owner, options } = context
  for (;;) {
    const now = options.now()
    const request = { name, owner, now, expiresAt: now + options.leaseDurationMs }
    if (await store.tryAcquire(request)) return
    await new Promise((resolve) => setTimeout(resolve, options.retryMs))
  }
}

const monitorLease = async (
  context: LeaseContext,
  signal: AbortSignal,
  state: HeartbeatState
): Promise<void> => {
  const { store, name, owner, options } = context
  while (!signal.aborted) {
    await waitForDelay(options.renewalMs, signal)
    if (signal.aborted) return
    try {
      const renewed = await store.renew({
        name,
        owner,
        expiresAt: options.now() + options.leaseDurationMs,
      })
      if (!renewed) state.failure = new Error('跨标签会话锁所有权已丢失')
    } catch (error) {
      state.failure = new Error('跨标签会话锁续租失败', { cause: error })
    }
    if (state.failure) return
  }
}

export const withLeaseLock = async <T>(request: LeaseLockRequest<T>): Promise<T> => {
  const { name, action, store, options = DEFAULT_OPTIONS } = request
  const owner = createOwnerId()
  const context = { name, owner, store, options }
  await acquireLease(context)
  const controller = new AbortController()
  const state: HeartbeatState = { failure: null }
  const heartbeat = monitorLease(context, controller.signal, state)
  let result: T
  try {
    result = await action()
  } finally {
    controller.abort()
    await heartbeat
    await store.release({ name, owner })
  }
  if (state.failure) throw state.failure
  return result
}

const indexedDbLeaseStore = new IndexedDbAuthLeaseStore()

export const withCrossTabLock = async <T>(name: string, action: () => Promise<T>): Promise<T> => {
  if (typeof navigator !== 'undefined' && navigator.locks) {
    return navigator.locks.request(name, action)
  }
  return withLeaseLock({ name, action, store: indexedDbLeaseStore })
}
