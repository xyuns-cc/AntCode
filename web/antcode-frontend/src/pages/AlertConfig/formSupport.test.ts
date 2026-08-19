import { describe, it, expect, vi, beforeEach } from 'vitest'

vi.mock('@/utils/notification', () => ({ default: vi.fn() }))

import showNotification from '@/utils/notification'
import { SECRET_MASK, validateWebhookUrl, notifyActionFailure } from './formSupport'

const notify = vi.mocked(showNotification)

/** antd Form.validateFields() 校验失败时 reject 的形状（不是 Error）。 */
const antdValidationRejection = {
  errorFields: [{ name: ['url'], errors: ['请输入 Webhook URL'] }],
  values: {},
  outOfDate: false
}

/** axios 在 422 时抛出的错误：message 是英文，真正原因在 response.data。 */
const axiosLikeError = Object.assign(new Error('Request failed with status code 422'), {
  response: { status: 422, data: { message: 'SMTP 主机不合法', detail: '' } }
})

beforeEach(() => {
  notify.mockClear()
})

describe('validateWebhookUrl', () => {
  // 判据：编辑既有 Webhook 时表单被回填成哨兵值，必须放行，
  // 否则「只改名字/级别/开关」根本提交不出去。
  it('放行后端回显的 ***REDACTED*** 哨兵，使编辑既有 Webhook 成为可能', async () => {
    await expect(validateWebhookUrl(null, SECRET_MASK)).resolves.toBeUndefined()
  })

  it('放行正常的 http/https Webhook URL', async () => {
    await expect(
      validateWebhookUrl(null, 'https://oapi.dingtalk.com/robot/send?access_token=x')
    ).resolves.toBeUndefined()
  })

  it('仍然拒绝非 URL 文本', async () => {
    await expect(validateWebhookUrl(null, 'not-a-url-at-all')).rejects.toThrow('请输入有效的 URL')
  })

  it('仍然拒绝非 http/https 协议（对齐后端 ALLOWED_WEBHOOK_SCHEMES）', async () => {
    await expect(validateWebhookUrl(null, 'file:///etc/passwd')).rejects.toThrow('请输入有效的 URL')
  })
})

describe('notifyActionFailure', () => {
  // 判据：表单校验失败不得再弹「未知错误」——字段级红字已经说清楚了。
  it('对 antd 表单校验失败保持静默，不弹全局错误', () => {
    notifyActionFailure(antdValidationRejection, '操作失败')
    expect(notify).not.toHaveBeenCalled()
  })

  // 判据：弹的必须是后端中文原因，而不是 axios 的英文 message。
  it('对 API 错误展示后端返回的原因而非 axios 原始英文 message', () => {
    notifyActionFailure(axiosLikeError, '保存失败')
    expect(notify).toHaveBeenCalledTimes(1)
    expect(notify).toHaveBeenCalledWith('error', '保存失败', 'SMTP 主机不合法')
    expect(notify.mock.calls[0][2]).not.toContain('Request failed with status code')
  })

  it('对普通 Error 仍展示其 message', () => {
    notifyActionFailure(new Error('渠道未启用'), '测试失败')
    expect(notify).toHaveBeenCalledWith('error', '测试失败', '渠道未启用')
  })
})
