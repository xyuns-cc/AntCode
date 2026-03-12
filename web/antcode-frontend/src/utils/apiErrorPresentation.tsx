import type { AxiosError } from 'axios'
import type { ReactNode } from 'react'

type ErrorDetail = {
  field?: string
  message?: string
}

type ErrorData = {
  error_code?: string
  errors?: ErrorDetail[]
}

type ErrorEnvelope = {
  message?: string
  data?: ErrorData | null
}

const DEFAULT_STATUS_MESSAGES: Record<number, string> = {
  400: '请求参数错误',
  401: '未认证或登录已过期',
  403: '权限不足',
  404: '请求的资源不存在',
  422: '请求参数验证失败',
  429: '请求过于频繁，请稍后再试',
  500: '服务器内部错误',
  502: '网关错误',
  503: '服务暂时不可用',
  504: '网关超时',
}

const trimText = (value: unknown): string => {
  if (typeof value !== 'string') return ''
  return value.trim()
}

const toLines = (errors: ErrorDetail[] | undefined): string[] => {
  return (errors ?? [])
    .map((item) => {
      const msg = trimText(item?.message)
      if (!msg) return ''
      const field = trimText(item?.field)
      return field ? `${field}: ${msg}` : msg
    })
    .filter(Boolean)
}

export type PresentedApiError = {
  title: string
  description?: ReactNode
}

export const presentApiError = (error: AxiosError<unknown>): PresentedApiError => {
  const status = error.response?.status
  const payload = error.response?.data

  const fallbackTitle = status ? DEFAULT_STATUS_MESSAGES[status] ?? `请求失败 (${status})` : '请求失败'

  if (payload && typeof payload === 'object' && 'message' in payload) {
    const envelope = payload as ErrorEnvelope
    const message = trimText(envelope.message)
    const errorCode = trimText(envelope.data?.error_code)
    const lines = toLines(envelope.data?.errors)

    const title = message || fallbackTitle
    if (!errorCode && lines.length === 0) {
      return { title }
    }

    const description: ReactNode = (
      <div>
        {errorCode ? <div style={{ marginBottom: lines.length ? 8 : 0, opacity: 0.85 }}>错误码：{errorCode}</div> : null}
        {lines.length ? (
          <ul style={{ margin: 0, paddingLeft: 18 }}>
            {lines.map((line, index) => (
              <li key={`${index}-${line}`}>{line}</li>
            ))}
          </ul>
        ) : null}
      </div>
    )

    return { title, description }
  }

  const networkMessage = trimText(error.message)
  if (networkMessage) {
    return { title: fallbackTitle, description: networkMessage }
  }
  return { title: fallbackTitle }
}

