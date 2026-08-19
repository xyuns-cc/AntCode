import { getErrorMessage } from '@/utils/helpers'
import showNotification from '@/utils/notification'

/**
 * 后端读取告警配置时把 Webhook URL / SMTP 密码替换成该哨兵值
 * (`alert_config_store.SECRET_MASK`)，写回时原样带上它表示「保持原值不变」
 * (`merge_webhooks` / `save_email_config`)。
 */
export const SECRET_MASK = '***REDACTED***'

const isHttpUrl = (value: string): boolean => {
  try {
    const { protocol } = new URL(value)
    return protocol === 'http:' || protocol === 'https:'
  } catch {
    return false
  }
}

/**
 * Webhook URL 校验。
 *
 * 编辑既有 Webhook 时表单被回填成 `***REDACTED***`，而 antd 的 `type: 'url'`
 * 规则会判它非法，于是「改个名字 / 改告警级别 / 关掉开关」全部提交不出去——
 * 除非用户重新手打一遍自己可能已经没有的密钥 URL。后端本来就支持这个哨兵，
 * 所以这里放行它，其余仍限制为 http/https（对齐后端 ALLOWED_WEBHOOK_SCHEMES）。
 */
export const validateWebhookUrl = async (_rule: unknown, value: unknown): Promise<void> => {
  if (typeof value !== 'string' || value === '' || value === SECRET_MASK) return
  if (isHttpUrl(value)) return
  throw new Error('请输入有效的 URL')
}

type AntdFormValidationError = { errorFields?: unknown }

/**
 * antd `Form.validateFields()` 校验失败时 reject 的是
 * `{ errorFields, values, outOfDate }`，并不是 `Error`。
 */
const isFormValidationError = (error: unknown): boolean =>
  !!error && typeof error === 'object' && Array.isArray((error as AntdFormValidationError).errorFields)

/**
 * 统一的操作失败提示。
 *
 * 原实现是 `error instanceof Error ? error.message : '未知错误'`，两头都不对：
 * 表单校验失败落到 else 分支，弹一个吓人的「操作失败 / 未知错误」盖住字段级
 * 红字提示；axios 错误落到 if 分支，弹出的是原始英文 `Request failed with
 * status code 422`，而不是后端给的中文原因。
 */
export const notifyActionFailure = (error: unknown, title: string): void => {
  // 校验失败已有字段级红字提示，再弹全局错误只会误导。
  if (isFormValidationError(error)) return
  showNotification('error', title, getErrorMessage(error))
}
