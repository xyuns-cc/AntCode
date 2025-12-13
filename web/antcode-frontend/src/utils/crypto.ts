/**
 * 客户端加密工具
 * 使用设备指纹生成密钥，对敏感数据进行混淆加密
 * 注意：前端加密无法提供完全安全保障，仅用于防止明文暴露
 */

/** 生成设备指纹 */
function getDeviceFingerprint(): string {
  const { navigator, screen } = window
  return [
    navigator.userAgent,
    navigator.language,
    screen.colorDepth,
    screen.width,
    screen.height,
    new Date().getTimezoneOffset(),
  ].join('|')
}

/** 字符串哈希 */
function simpleHash(str: string): number {
  let hash = 0
  for (let i = 0; i < str.length; i++) {
    hash = ((hash << 5) - hash) + str.charCodeAt(i)
    hash = hash & hash
  }
  return Math.abs(hash)
}

/** 生成加密密钥 */
function generateKey(): string {
  return simpleHash(getDeviceFingerprint()).toString(36)
}

/** XOR 加密/解密 */
function xorCipher(text: string, key: string): string {
  let result = ''
  for (let i = 0; i < text.length; i++) {
    result += String.fromCharCode(text.charCodeAt(i) ^ key.charCodeAt(i % key.length))
  }
  return result
}

/** Base64 编码 */
function base64Encode(str: string): string {
  return btoa(encodeURIComponent(str).replace(/%([0-9A-F]{2})/g, (_, p1) => 
    String.fromCharCode(parseInt(p1, 16))
  ))
}

/** Base64 解码 */
function base64Decode(str: string): string {
  return decodeURIComponent(
    Array.from(atob(str), c => '%' + ('00' + c.charCodeAt(0).toString(16)).slice(-2)).join('')
  )
}

/** 加密 */
export function encrypt(text: string): string {
  try {
    const key = generateKey()
    const encrypted = xorCipher(text, key)
    return base64Encode(encrypted)
  } catch {
    return text
  }
}

/** 解密 */
export function decrypt(encryptedText: string): string {
  try {
    const key = generateKey()
    const decoded = base64Decode(encryptedText)
    return xorCipher(decoded, key)
  } catch {
    return encryptedText
  }
}

/** 安全存储工具类 */
export class SecureStorage {
  /** 加密存储 */
  static setItem(key: string, value: string): void {
    localStorage.setItem(key, encrypt(value))
  }

  /** 解密读取 */
  static getItem(key: string): string | null {
    const encrypted = localStorage.getItem(key)
    return encrypted ? decrypt(encrypted) : null
  }

  /** 删除 */
  static removeItem(key: string): void {
    localStorage.removeItem(key)
  }

  /** 检查是否存在 */
  static hasItem(key: string): boolean {
    return localStorage.getItem(key) !== null
  }
}

export default SecureStorage

