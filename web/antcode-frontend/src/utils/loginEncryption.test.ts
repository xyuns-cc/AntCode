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
