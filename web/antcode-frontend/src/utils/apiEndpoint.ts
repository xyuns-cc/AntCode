const DEFAULT_PROTOCOL = 'http'
const DEFAULT_SERVER_PORT = '8000'
const IPV4_WILDCARD_HOST = '0.0.0.0'
const IPV6_WILDCARD_HOST = '::'
const BRACKETED_IPV6_WILDCARD_HOST = '[::]'
const LOCALHOST = 'localhost'

export type ApiEndpointConfig = Readonly<{
  explicitApiBaseUrl?: string
  serverPort?: string
  bindHost?: string
  serverDomain?: string
  pageHostname?: string
  protocol?: string
}>

const normalizeProtocol = (value: string | undefined): string => {
  const protocol = (value || DEFAULT_PROTOCOL).replace(/:$/, '').trim()
  return protocol || DEFAULT_PROTOCOL
}

const normalizeApiBaseUrl = (value: string): string => {
  return value.trim().replace(/\/api\/?$/, '').replace(/\/$/, '')
}

const isWildcardHost = (host: string): boolean => {
  return host === IPV4_WILDCARD_HOST || host === IPV6_WILDCARD_HOST || host === BRACKETED_IPV6_WILDCARD_HOST
}

const formatUrlHost = (host: string): string => {
  if (host.includes(':') && !host.startsWith('[')) {
    return `[${host}]`
  }
  return host
}

const resolveClientHost = (config: ApiEndpointConfig): string => {
  const pageHostname = (config.pageHostname || '').trim()
  if (pageHostname) return pageHostname

  const bindHost = (config.bindHost || '').trim()
  if (bindHost && !isWildcardHost(bindHost)) return bindHost

  const serverDomain = (config.serverDomain || '').trim()
  return serverDomain || LOCALHOST
}

export const resolveApiBaseUrl = (config: ApiEndpointConfig): string => {
  const explicitBaseUrl = normalizeApiBaseUrl(config.explicitApiBaseUrl || '')
  if (explicitBaseUrl) return explicitBaseUrl

  // P2-07：生产构建（import.meta.env.PROD）走同源相对路径，让请求经过
  // 前端 Nginx 的 /api proxy_pass，避免暴露 Web API 8000 端口 + HTTPS 场景下
  // 混合内容错误。开发模式（dev server）保留旧行为 host:8000 直连。
  const isProduction =
    typeof import.meta !== 'undefined'
    && Boolean((import.meta as unknown as { env?: { PROD?: boolean } }).env?.PROD)
  if (isProduction) {
    // 返回空串代表相对路径（api.ts 会拼接 '/api/v1/...'），走当前 origin。
    return ''
  }

  const serverPort = (config.serverPort || DEFAULT_SERVER_PORT).trim() || DEFAULT_SERVER_PORT
  const protocol = normalizeProtocol(config.protocol)
  const host = formatUrlHost(resolveClientHost(config))
  return `${protocol}://${host}:${serverPort}`
}
