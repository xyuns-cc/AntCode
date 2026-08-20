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
