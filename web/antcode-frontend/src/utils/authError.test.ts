/**
 * isAuthError 只认 HTTP 401，不再拿错误文案做子串匹配。
 *
 * 后端 http_exception_handler 把**每一条** HTTPException 的 detail 原样搬进
 * `data.message`（services/web_api/.../exceptions.py::_http_error_message，`detail` 键
 * 在响应体里根本不存在）。所以 '认证'/'登录'/'token'/'expired' 这几个子串筛的是全站
 * 错误文案，命中即强制登出——下面三条负例全都是从后端现有文案抄来的真实报文。
 *
 * 判据成对：正例钉住 401 仍然必须登出（否则「一律返回 false」也能过），负例钉住这三种
 * 非鉴权错误不得登出。403 那条同时防止修法过头改成 `401 || 403`。
 */
import { describe, expect, it } from 'vitest'
import AuthHandler from './authHandler'

const apiError = (status: number, message: string) => ({
  response: { status, data: { success: false, code: status, message, data: null } },
})

describe('isAuthError', () => {
  it('401 仍然算认证失败', () => {
    expect(AuthHandler.isAuthError(apiError(401, '未认证或登录已过期'))).toBe(true)
  })

  // login_crypto.py::STALE_LOGIN_KEY_MESSAGE，经 password_transport.py 以 400 抛出。
  // 改密 / 建号时 RSA 公钥轮换就会给这条，前端本来是丢掉缓存公钥让用户原地重提交的
  // （utils/loginEncryption.ts::STALE_KEY_MARKER）——登出会直接掐掉这条恢复路径。
  it('改密时的 400「登录密钥已过期」不算认证失败', () => {
    expect(AuthHandler.isAuthError(apiError(400, '登录密钥已过期，请重试'))).toBe(false)
  })

  // repositories.py 扫描失败把 git stderr 原样回传（422）；私有库鉴权失败时 GitHub 的
  // 提示里就带着 "personal access token"。
  it('仓库扫描 422 回传的 git stderr 不算认证失败', () => {
    const stderr = 'remote: Support for password authentication was removed. Please use a personal access token instead.'
    expect(AuthHandler.isAuthError(apiError(422, stderr))).toBe(false)
  })

  // base.py:54 AUTH_SELF_SERVICE_DISABLED_DETAIL，404。
  it('自助认证接口关闭的 404 不算认证失败', () => {
    expect(AuthHandler.isAuthError(apiError(404, '企业内部系统已禁用自助认证接口，请联系系统管理员'))).toBe(false)
  })

  // 本仓 403 是「已登录但无权限」：/dashboard/metrics、/workers/stats 是管理员专属，
  // 普通用户稳定拿 403。按登出处理会把普通用户直接踢出去。
  it('管理员专属接口给普通用户的 403 不算认证失败', () => {
    expect(AuthHandler.isAuthError(apiError(403, '权限不足'))).toBe(false)
  })

  it('没有 response 的错误不算认证失败', () => {
    expect(AuthHandler.isAuthError(new Error('Network Error'))).toBe(false)
  })
})
