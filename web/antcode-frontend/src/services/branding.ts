/**
 * 品牌配置服务（公开接口）
 */
import axios from 'axios'
import { APP_BRANDING, type AppBrandingConfig } from '@/config/app'
import type { BrandingPayload } from '@/types'
import { API_BASE_URL } from '@/utils/constants'

const brandingApi = axios.create({
  baseURL: API_BASE_URL,
  timeout: 5000,
})

const normalizeText = (value?: string | null) => {
  const text = (value ?? '').trim()
  return text.length > 0 ? text : undefined
}

export const normalizeBranding = (payload?: Partial<BrandingPayload>): AppBrandingConfig => {
  const brandName = normalizeText(payload?.brand_name) || APP_BRANDING.brandName
  const appTitle = normalizeText(payload?.app_title) || APP_BRANDING.appTitle
  const logoText = normalizeText(payload?.logo_text) || APP_BRANDING.logoText
  const logoShort = normalizeText(payload?.logo_short) || logoText.slice(0, 1) || APP_BRANDING.logoShort
  const logoIcon = normalizeText(payload?.logo_icon) || APP_BRANDING.logoIcon
  const logoUrl = normalizeText(payload?.logo_url) || APP_BRANDING.logoUrl
  const faviconUrl = normalizeText(payload?.favicon_url) || APP_BRANDING.faviconUrl

  return {
    brandName,
    appTitle,
    logoText,
    logoShort,
    logoIcon,
    logoUrl,
    faviconUrl,
  }
}

/**
 * 获取品牌配置
 */
export const getBrandingConfig = async (): Promise<AppBrandingConfig> => {
  const response = await brandingApi.get<{ data: BrandingPayload | null }>('/api/v1/branding/public')
  return normalizeBranding(response.data?.data || undefined)
}
