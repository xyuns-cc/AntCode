/**
 * 告警配置服务
 */
import { BaseService } from './base'

// Webhook 配置
export interface WebhookConfig {
  name: string
  url: string
  levels: string[]
  enabled: boolean
}

// 邮件收件人配置
export interface EmailRecipient {
  email: string
  name: string
  levels: string[]
}

// 邮件告警配置
export interface EmailConfig {
  smtp_host: string
  smtp_port: number
  smtp_user: string
  smtp_password: string
  smtp_ssl: boolean
  sender_name: string
  recipients: EmailRecipient[]
}

// 告警渠道配置
export interface AlertChannelConfig {
  feishu_webhooks: WebhookConfig[]
  dingtalk_webhooks: WebhookConfig[]
  wecom_webhooks: WebhookConfig[]
  email_config?: EmailConfig
}

// 限流配置
export interface AlertRateLimitConfig {
  enabled: boolean
  window: number
  max_count: number
}

// 重试配置
export interface AlertRetryConfig {
  enabled: boolean
  max_retries: number
  retry_delay: number
}

// 告警配置请求
export interface AlertConfigRequest {
  channels?: AlertChannelConfig
  auto_alert_levels?: string[]
  rate_limit?: AlertRateLimitConfig
  retry?: AlertRetryConfig
}

// 告警配置响应
export interface AlertConfigResponse {
  channels: AlertChannelConfig
  auto_alert_levels: string[]
  rate_limit: AlertRateLimitConfig
  retry: AlertRetryConfig
  enabled_channels: string[]
  available_channels: string[]
}

// 告警历史记录
export interface AlertHistoryItem {
  timestamp: string
  level: string
  source: string
  message: string
  extra?: Record<string, unknown>
  status: string
}

// 告警历史响应
export interface AlertHistoryResponse {
  items: AlertHistoryItem[]
  total: number
}

// 告警统计响应
export interface AlertStatsResponse {
  total_alerts: number
  by_level: Record<string, number>
  by_source: Record<string, number>
  enabled_channels: string[]
  rate_limit_stats?: Record<string, unknown>
}

// 测试告警请求
export interface AlertTestRequest {
  channel: string
  message?: string
}

// 测试告警响应
export interface AlertTestResponse {
  success: boolean
  message: string
  result?: Record<string, unknown>
}

class AlertService extends BaseService {
  constructor() {
    super('/api/v1/alert')
  }

  /**
   * 获取告警配置
   */
  async getConfig(): Promise<AlertConfigResponse> {
    return this.get<AlertConfigResponse>('/config')
  }

  /**
   * 更新告警配置
   */
  async updateConfig(data: AlertConfigRequest): Promise<{ updated: boolean }> {
    return this.put<{ updated: boolean }>('/config', data)
  }

  /**
   * 重新加载告警配置
   */
  async reloadConfig(): Promise<{ reloaded: boolean }> {
    return this.post<{ reloaded: boolean }>('/reload')
  }

  /**
   * 获取告警历史
   */
  async getHistory(params?: {
    limit?: number
    level?: string
    source?: string
  }): Promise<AlertHistoryResponse> {
    const url = this.buildQueryUrl('/history', params)
    return this.get<AlertHistoryResponse>(url)
  }

  /**
   * 获取告警统计
   */
  async getStats(): Promise<AlertStatsResponse> {
    return this.get<AlertStatsResponse>('/stats')
  }

  /**
   * 发送测试告警
   */
  async sendTestAlert(data: AlertTestRequest): Promise<AlertTestResponse> {
    return this.post<AlertTestResponse>('/test', data)
  }
}

export const alertService = new AlertService()
export default alertService
