// P1-round6 5.4: decodeAccessToken 必须处理 base64url + UTF-8 且异常路径吞错。
import { describe, expect, it } from 'vitest'
import { decodeAccessToken } from './authToken'

function b64url(obj: unknown): string {
  const json = JSON.stringify(obj)
  const bytes = new TextEncoder().encode(json)
  let bin = ''
  for (let i = 0; i < bytes.length; i++) bin += String.fromCharCode(bytes[i])
  return btoa(bin).replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, '')
}

function fakeJwt(payload: unknown): string {
  return `header.${b64url(payload)}.sig`
}

describe('decodeAccessToken (P1-round6 5.4)', () => {
  it('解 base64url 编码的 payload (含 `-`/`_` 字符)', () => {
    const payload = { sub: 'user-1', exp: 1730000000, iss: 'https://a?/b>c' }
    const decoded = decodeAccessToken(fakeJwt(payload))
    expect(decoded).toEqual(payload)
  })

  it('解 Unicode 用户名 (中日韩) 不乱码', () => {
    const payload = { sub: '用户张三', role: 'admin', name: '田中太郎' }
    const decoded = decodeAccessToken(fakeJwt(payload))
    expect(decoded).toEqual(payload)
  })

  it('缺 padding 的 base64url 也能解 (归一化 + 补齐)', () => {
    const payload = { a: 1 }
    // 手工去掉 `=` padding
    const raw = fakeJwt(payload).replace(/=+/g, '')
    const decoded = decodeAccessToken(raw)
    expect(decoded).toEqual(payload)
  })

  it('非法 token 吞成 null (不 throw, 避免误触发会话恢复)', () => {
    expect(decodeAccessToken('not-a-jwt')).toBeNull()
    expect(decodeAccessToken('header.$$invalid$$.sig')).toBeNull()
    expect(decodeAccessToken('')).toBeNull()
  })
})
