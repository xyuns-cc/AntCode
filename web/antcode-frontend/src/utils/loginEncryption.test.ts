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

  it('refuses to encrypt an empty password instead of sending an empty cipher', async () => {
    await expect(encryptPasswords(['ok', ''])).rejects.toThrow('密码不能为空')
  })
})
