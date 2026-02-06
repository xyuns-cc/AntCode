/**
 * 品牌配置（后端返回字段）
 */
export interface BrandingPayload {
  brand_name: string
  app_title: string
  logo_text: string
  logo_short: string
  logo_icon: string
  logo_url?: string | null
  favicon_url?: string | null
}
