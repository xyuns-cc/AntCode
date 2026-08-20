import { constants, generateKeyPairSync, privateDecrypt } from 'node:crypto'
import { beforeEach, describe, expect, it, vi } from 'vitest'

const mocks = vi.hoisted(() => ({
  get: vi.fn(),
}))

vi.mock('@/services/api', () => ({
  default: { get: mocks.get },
  unwrapResponse: <T>(response: { data: { data: T } }): T => response.data.data,
}))

import {
  clearLoginPublicKeyCache,
  encryptLoginPassword,
  encryptPasswords,
} from './loginEncryption'

describe('login password encryption', () => {
  beforeEach(() => clearLoginPublicKeyCache())

  it('uses RSA-OAEP-256 when secure-context SubtleCrypto is unavailable', async () => {
    const { publicKey, privateKey } = generateKeyPairSync('rsa', { modulusLength: 2048 })
    const publicKeyPem = publicKey.export({ type: 'spki', format: 'pem' }).toString()
    mocks.get.mockResolvedValue({
      data: {
        data: {
          algorithm: 'RSA-OAEP-256',
          key_id: 'key-1',
          public_key: publicKeyPem,
        },
      },
    })
    const originalCrypto = window.crypto
    Object.defineProperty(window, 'crypto', {
      configurable: true,
      value: { getRandomValues: originalCrypto.getRandomValues.bind(originalCrypto) },
    })

    try {
      const encrypted = await encryptLoginPassword('P@ssw0rd-测试')
      const plaintext = privateDecrypt({
        key: privateKey,
        oaepHash: 'sha256',
        padding: constants.RSA_PKCS1_OAEP_PADDING,
      }, Buffer.from(encrypted.encryptedPassword, 'base64'))

      expect(plaintext.toString('utf8')).toBe('P@ssw0rd-测试')
      expect(encrypted.algorithm).toBe('RSA-OAEP-256')
    } finally {
      Object.defineProperty(window, 'crypto', {
        configurable: true,
        value: originalCrypto,
      })
    }
  })
})

describe('multi-password encryption', () => {
  beforeEach(() => clearLoginPublicKeyCache())

  it('encrypts a whole batch under one key_id with a single key fetch', async () => {
    // 改密要同时送 old/new。分两次取公钥会在密钥轮换窗口里拿到两个 key_id，
    // 服务端只认一个，改密会以"密钥已过期"随机失败。
    const { publicKey, privateKey } = generateKeyPairSync('rsa', { modulusLength: 2048 })
    mocks.get.mockResolvedValue({
      data: {
        data: {
          algorithm: 'RSA-OAEP-256',
          key_id: 'key-1',
          public_key: publicKey.export({ type: 'spki', format: 'pem' }).toString(),
        },
      },
    })

    // jsdom 的 SubtleCrypto 不可用，和上面的用例一样走 node-forge 分支。
    const originalCrypto = window.crypto
    Object.defineProperty(window, 'crypto', {
      configurable: true,
      value: { getRandomValues: originalCrypto.getRandomValues.bind(originalCrypto) },
    })

    try {
      const result = await encryptPasswords(['old-secret', 'new-secret'])

      expect(mocks.get).toHaveBeenCalledTimes(1)
      expect(result.keyId).toBe('key-1')
      const decrypt = (value: string) => privateDecrypt({
        key: privateKey,
        oaepHash: 'sha256',
        padding: constants.RSA_PKCS1_OAEP_PADDING,
      }, Buffer.from(value, 'base64')).toString('utf8')
      expect(result.encrypted.map(decrypt)).toEqual(['old-secret', 'new-secret'])
    } finally {
      Object.defineProperty(window, 'crypto', { configurable: true, value: originalCrypto })
    }
  })

  it('keeps the whole batch decryptable by the key it reports, even if the server rotates mid-batch', async () => {
    // 上一条只断言 get 被调了一次——那是模块级公钥缓存的性质，不是"整批共用一把钥匙"
    // 的性质：任何绕开缓存、逐个口令取公钥的实现，在冷缓存下也会被 Promise.all 合并
    // 成一次请求，照样绿。这里让服务端每次返回不同的 key_id / 不同的密钥对：
    // 只要有任何一个口令是用另一把公钥加密的，服务端拿 result.keyId 对应的私钥就解
    // 不开——正是"改密随机失败"的真实故障形态。
    const keyA = generateKeyPairSync('rsa', { modulusLength: 2048 })
    const keyB = generateKeyPairSync('rsa', { modulusLength: 2048 })
    const privateKeys: Record<string, typeof keyA.privateKey> = {
      'key-A': keyA.privateKey,
      'key-B': keyB.privateKey,
    }
    const envelope = (id: string, pair: typeof keyA) => ({
      data: {
        data: {
          algorithm: 'RSA-OAEP-256',
          key_id: id,
          public_key: pair.publicKey.export({ type: 'spki', format: 'pem' }).toString(),
        },
      },
    })
    mocks.get
      .mockResolvedValueOnce(envelope('key-A', keyA))
      .mockResolvedValueOnce(envelope('key-B', keyB))

    const originalCrypto = window.crypto
    Object.defineProperty(window, 'crypto', {
      configurable: true,
      value: { getRandomValues: originalCrypto.getRandomValues.bind(originalCrypto) },
    })

    try {
      const result = await encryptPasswords(['old-secret', 'new-secret'])
      const privateKey = privateKeys[result.keyId]
      expect(privateKey).toBeDefined()

      const decrypt = (value: string) => privateDecrypt({
        key: privateKey,
        oaepHash: 'sha256',
        padding: constants.RSA_PKCS1_OAEP_PADDING,
      }, Buffer.from(value, 'base64')).toString('utf8')
      expect(result.encrypted.map(decrypt)).toEqual(['old-secret', 'new-secret'])
    } finally {
      Object.defineProperty(window, 'crypto', { configurable: true, value: originalCrypto })
    }
  })

  it('refuses to encrypt an empty password instead of sending an empty cipher', async () => {
    await expect(encryptPasswords(['ok', ''])).rejects.toThrow('密码不能为空')
  })
})
