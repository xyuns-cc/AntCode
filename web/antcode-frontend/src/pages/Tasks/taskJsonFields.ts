/**
 * 任务表单里两个自由输入的 JSON 字段的解析与校验。
 *
 * 抽成纯函数是为了让「解析失败」有明确返回值而不是就地弹提示 + return，
 * 页面只负责把 error 交给 message，解析规则本身可以单测覆盖。
 */

export type ParsedField<T> = { ok: true; value: T | undefined } | { ok: false; error: string }

const parseJsonObject = (raw: string, label: string): ParsedField<Record<string, unknown>> => {
  let parsed: unknown
  try {
    parsed = JSON.parse(raw)
  } catch {
    return { ok: false, error: `${label} JSON 格式错误` }
  }
  if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed)) {
    return { ok: false, error: `${label}必须是 JSON 对象` }
  }
  return { ok: true, value: parsed as Record<string, unknown> }
}

/** 执行参数：任意 JSON 对象即可，值不限类型。 */
export const parseExecutionParams = (raw?: string): ParsedField<Record<string, unknown>> => {
  if (!raw) return { ok: true, value: undefined }
  return parseJsonObject(raw, '执行参数')
}

/** 环境变量：必须是 JSON 对象，且每个值都必须是字符串（会原样注入进程环境）。 */
export const parseEnvironmentVars = (raw?: string): ParsedField<Record<string, string>> => {
  if (!raw) return { ok: true, value: undefined }
  const parsed = parseJsonObject(raw, '环境变量')
  if (!parsed.ok) return parsed
  const map: Record<string, string> = {}
  for (const [key, value] of Object.entries(parsed.value ?? {})) {
    if (typeof value !== 'string') {
      return { ok: false, error: `环境变量 ${key} 的值必须是字符串` }
    }
    map[key] = value
  }
  return { ok: true, value: map }
}
