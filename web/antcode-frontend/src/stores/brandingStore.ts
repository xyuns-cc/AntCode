/**
 * 品牌配置 Store
 */
import { create } from 'zustand'
import { APP_BRANDING, type AppBrandingConfig } from '@/config/app'
import { getBrandingConfig } from '@/services/branding'

interface BrandingState {
  branding: AppBrandingConfig
  isLoading: boolean
  hasLoaded: boolean
  fetchBranding: () => Promise<void>
}

export const useBrandingStore = create<BrandingState>((set, get) => ({
  branding: APP_BRANDING,
  isLoading: false,
  hasLoaded: false,

  fetchBranding: async () => {
    if (get().isLoading || get().hasLoaded) return

    set({ isLoading: true })
    try {
      const branding = await getBrandingConfig()
      set({ branding, hasLoaded: true })
    } catch {
      set({ branding: APP_BRANDING, hasLoaded: true })
    } finally {
      set({ isLoading: false })
    }
  },
}))
