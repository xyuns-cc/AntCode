import { QueryCache, QueryClient } from '@tanstack/react-query'
import type { AxiosError } from 'axios'
import showNotification from '@/utils/notification'

interface ErrorResponseData {
  message?: string
  detail?: string | Array<{ msg?: string }>
}

interface DetailItem {
  msg?: string
}

const buildErrorMessage = (error: unknown) => {
  if (!error) return '请求失败'
  const axiosError = error as AxiosError<ErrorResponseData>
  const responseData = axiosError.response?.data
  if (responseData?.message) return String(responseData.message)
  if (responseData?.detail) return Array.isArray(responseData.detail)
    ? responseData.detail.map((d: DetailItem) => d?.msg || d).join(', ')
    : String(responseData.detail)
  if (axiosError.response?.status === 401) return '认证已过期，请重新登录'
  if (axiosError.response?.status === 403) return '权限不足'
  if (axiosError.response?.status === 404) return '资源不存在'
  if (axiosError.message) return axiosError.message
  return '请求失败'
}

export const queryClient = new QueryClient({
  queryCache: new QueryCache({
    onError: (error) => {
      const axiosError = (error as AxiosError)
      if (axiosError?.isAxiosError) {
        // Axios 拦截器已统一提示，这里避免重复提示
        return
      }
      const message = buildErrorMessage(error)
      showNotification('error', message)
    }
  }),
  defaultOptions: {
    queries: {
      refetchOnWindowFocus: false,
      staleTime: 30_000,
      retry: (failureCount, error) => {
        const axiosError = error as AxiosError
        if (axiosError?.response?.status === 401) return false
        return failureCount < 2
      }
    },
    mutations: {
      retry: 0
    }
  }
})
