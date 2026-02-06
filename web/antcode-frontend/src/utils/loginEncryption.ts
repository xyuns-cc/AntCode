import apiClient, { unwrapResponse } from '@/services/api'
import type { ApiResponse, LoginPublicKeyResponse } from '@/types'

const LOGIN_PUBLIC_KEY_ENDPOINT = '/api/v1/auth/public-key'
const SUPPORTED_ALGORITHM = 'RSA-OAEP-256'

type CachedKey = {
  payload: LoginPublicKeyResponse
  cryptoKey: CryptoKey
}

let cachedKey: CachedKey | null = null
let pendingKeyPromise: Promise<CachedKey> | null = null

const ensureWebCrypto = () => {
  if (!window.crypto?.subtle) {
    throw new Error('当前浏览器不支持安全加密，请更换浏览器后再试')
  }
}

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

const importPublicKey = async (publicKeyPem: string): Promise<CryptoKey> => {
  const keyData = pemToArrayBuffer(publicKeyPem)
  return window.crypto.subtle.importKey(
    'spki',
    keyData,
    { name: 'RSA-OAEP', hash: 'SHA-256' },
    false,
    ['encrypt']
  )
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

export const encryptLoginPassword = async (password: string): Promise<{
  encryptedPassword: string
  algorithm: string
  keyId: string
}> => {
  ensureWebCrypto()
  const trimmed = password ?? ''
  if (!trimmed) {
    throw new Error('密码不能为空')
  }

  const { payload, cryptoKey } = await getLoginPublicKey()
  const encoded = new TextEncoder().encode(trimmed)
  const encrypted = await window.crypto.subtle.encrypt({ name: 'RSA-OAEP' }, cryptoKey, encoded)

  return {
    encryptedPassword: arrayBufferToBase64(encrypted),
    algorithm: payload.algorithm,
    keyId: payload.key_id,
  }
}

export const clearLoginPublicKeyCache = () => {
  cachedKey = null
}

