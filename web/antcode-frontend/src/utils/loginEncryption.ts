// 口令传输加密。登录、改密、管理员重置密码提交的是同一类机密，共用同一个
// /auth/public-key 公钥与同一套字段名——改密此前直接发明文 JSON，是漏做不是权衡。
import apiClient, { unwrapResponse } from '@/services/api'
import type { ApiResponse, LoginPublicKeyResponse } from '@/types'

const LOGIN_PUBLIC_KEY_ENDPOINT = '/api/v1/auth/public-key'
const SUPPORTED_ALGORITHM = 'RSA-OAEP-256'

type CachedKey = {
  payload: LoginPublicKeyResponse
  cryptoKey: CryptoKey | null
}

let cachedKey: CachedKey | null = null
let pendingKeyPromise: Promise<CachedKey> | null = null

const pemToArrayBuffer = (pem: string): ArrayBuffer => {
  const normalized = pem
    .replace(/-----BEGIN PUBLIC KEY-----/g, '')
    .replace(/-----END PUBLIC KEY-----/g, '')
    .replace(/\s+/g, '')
  const binary = atob(normalized)
  const bytes = new Uint8Array(binary.length)
  for (let i = 0; i < binary.length; i += 1) {
    bytes[i] = binary.charCodeAt(i)
  }
  return bytes.buffer
}

const arrayBufferToBase64 = (buffer: ArrayBuffer): string => {
  const bytes = new Uint8Array(buffer)
  const chunkSize = 0x8000
  let binary = ''
  for (let i = 0; i < bytes.length; i += chunkSize) {
    const chunk = bytes.subarray(i, i + chunkSize)
    binary += String.fromCharCode(...chunk)
  }
  return btoa(binary)
}

const fetchLoginPublicKey = async (): Promise<LoginPublicKeyResponse> => {
  const response = await apiClient.get<ApiResponse<LoginPublicKeyResponse>>(LOGIN_PUBLIC_KEY_ENDPOINT)
  return unwrapResponse<LoginPublicKeyResponse>(response)
}

const importPublicKey = async (publicKeyPem: string): Promise<CryptoKey | null> => {
  if (!window.crypto?.subtle) return null
  const keyData = pemToArrayBuffer(publicKeyPem)
  return window.crypto.subtle.importKey(
    'spki',
    keyData,
    { name: 'RSA-OAEP', hash: 'SHA-256' },
    false,
    ['encrypt']
  )
}

const encryptWithForge = async (password: string, publicKeyPem: string): Promise<string> => {
  const forge = await import('node-forge')
  const publicKey = forge.pki.publicKeyFromPem(publicKeyPem)
  const encrypted = publicKey.encrypt(forge.util.encodeUtf8(password), 'RSA-OAEP', {
    md: forge.md.sha256.create(),
    mgf1: { md: forge.md.sha256.create() },
  })
  return forge.util.encode64(encrypted)
}

const getLoginPublicKey = async (): Promise<CachedKey> => {
  if (cachedKey) return cachedKey
  if (pendingKeyPromise) return pendingKeyPromise

  pendingKeyPromise = (async () => {
    const payload = await fetchLoginPublicKey()
    if (payload.algorithm !== SUPPORTED_ALGORITHM) {
      throw new Error(`不支持的登录加密算法: ${payload.algorithm}`)
    }
    if (!payload.public_key) {
      throw new Error('登录公钥无效，请稍后重试')
    }
    const cryptoKey = await importPublicKey(payload.public_key)
    return { payload, cryptoKey }
  })()

  try {
    cachedKey = await pendingKeyPromise
    return cachedKey
  } finally {
    pendingKeyPromise = null
  }
}

const encryptOne = async (
  password: string,
  payload: LoginPublicKeyResponse,
  cryptoKey: CryptoKey | null,
): Promise<string> => (
  cryptoKey
    ? arrayBufferToBase64(await window.crypto.subtle.encrypt(
      { name: 'RSA-OAEP' },
      cryptoKey,
      new TextEncoder().encode(password),
    ))
    : encryptWithForge(password, payload.public_key)
)

/**
 * 一次取一次公钥，加密一组口令。
 *
 * 改密要同时送 old/new 两个口令，它们必须用同一把公钥、报同一个 key_id：
 * 分两次调用会在密钥轮换的窗口里拿到两个 key_id，服务端只认一个。
 */
export const encryptPasswords = async (passwords: string[]): Promise<{
  encrypted: string[]
  algorithm: string
  keyId: string
}> => {
  if (passwords.some((password) => !password)) {
    throw new Error('密码不能为空')
  }

  const { payload, cryptoKey } = await getLoginPublicKey()
  const encrypted = await Promise.all(
    passwords.map((password) => encryptOne(password, payload, cryptoKey)),
  )

  return { encrypted, algorithm: payload.algorithm, keyId: payload.key_id }
}

export const encryptLoginPassword = async (password: string): Promise<{
  encryptedPassword: string
  algorithm: string
  keyId: string
}> => {
  const { encrypted, algorithm, keyId } = await encryptPasswords([password ?? ''])
  return { encryptedPassword: encrypted[0], algorithm, keyId }
}

export const clearLoginPublicKeyCache = () => {
  cachedKey = null
}

// 与后端 login_crypto.STALE_LOGIN_KEY_MESSAGE 对齐；后端有用例钉住那段文案。
const STALE_KEY_MARKER = '登录密钥已过期'

const isStaleLoginKeyError = (error: unknown): boolean => {
  const message = (error as { response?: { data?: { message?: unknown } } })?.response?.data?.message
  return typeof message === 'string' && message.includes(STALE_KEY_MARKER)
}

/**
 * 提交口令密文；服务端报「登录密钥已过期」时丢弃缓存的公钥。
 *
 * 密钥轮换后缓存不清，用户就会拿同一把过期公钥反复撞同一个 400，不整页刷新
 * 出不去——而改密/建号发生在设置页与用户管理页，那里根本没有"登录页面"可刷。
 *
 * 这里刻意**不重试**：静默重试会把一个明确错误变成不可解释的卡顿。错误照常
 * 抛给调用方展示，只是下一次提交会重新取公钥，用户再点一次就能成功。
 */
export const withStaleKeyRecovery = async <T>(submit: () => Promise<T>): Promise<T> => {
  try {
    return await submit()
  } catch (error) {
    if (isStaleLoginKeyError(error)) clearLoginPublicKeyCache()
    throw error
  }
}
