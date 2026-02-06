import { useState, useCallback, useRef, useEffect } from 'react'
import showNotification from '@/utils/notification'
import { getErrorMessage } from '@/utils/helpers'
import Logger from '@/utils/logger'

interface UseApiOptions<T> {
  onSuccess?: (data: T) => void
  onError?: (error: unknown) => void
  showSuccessMessage?: boolean | string
  showErrorMessage?: boolean
  immediate?: boolean
}

interface UseApiResult<T, P extends unknown[] = unknown[]> {
  data: T | null
  loading: boolean
  error: unknown
  execute: (...params: P) => Promise<T>
  reset: () => void
  cancel: () => void
}

export function useApi<T, P extends unknown[] = unknown[]>(
  apiFunction: (...params: P) => Promise<T>,
  options: UseApiOptions<T> = {}
): UseApiResult<T, P> {
  const {
    onSuccess,
    onError,
    showSuccessMessage = false,
    showErrorMessage = true,
    immediate = false
  } = options

  const [data, setData] = useState<T | null>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<unknown>(null)
  
  const cancelRef = useRef<boolean>(false)
  const mountedRef = useRef(true)

  // 执行API调用
  const execute = useCallback(async (...params: P): Promise<T> => {
    if (loading) {
      Logger.warn('API call already in progress')
      return Promise.reject(new Error('API call already in progress'))
    }

    setLoading(true)
    setError(null)
    cancelRef.current = false

    try {
      const result = await apiFunction(...params)
      
      // 检查是否已取消或组件已卸载
      if (cancelRef.current || !mountedRef.current) {
        return Promise.reject(new Error('Request cancelled'))
      }

      setData(result)
      
      // 显示成功消息
      if (showSuccessMessage) {
        const successMessage = typeof showSuccessMessage === 'string' 
          ? showSuccessMessage 
          : '操作成功'
        showNotification('success', successMessage)
      }

      // 调用成功回调
      onSuccess?.(result)
      
      return result
    } catch (err: unknown) {
      // 检查是否已取消或组件已卸载
      if (cancelRef.current || !mountedRef.current) {
        return Promise.reject(err)
      }

      setError(err)
      
      // 显示错误消息
      if (showErrorMessage) {
        const errorMessage = getErrorMessage(err)
        showNotification('error', errorMessage)
      }

      // 调用错误回调
      onError?.(err)
      
      throw err
    } finally {
      if (mountedRef.current && !cancelRef.current) {
        setLoading(false)
      }
    }
  }, [apiFunction, loading, onSuccess, onError, showSuccessMessage, showErrorMessage])

  // 重置状态
  const reset = useCallback(() => {
    setData(null)
    setError(null)
    setLoading(false)
    cancelRef.current = false
  }, [])

  // 取消请求
  const cancel = useCallback(() => {
    cancelRef.current = true
    setLoading(false)
  }, [])

  // 立即执行（如果设置了immediate）
  useEffect(() => {
    if (immediate) {
      execute(...([] as unknown as P)).catch((err) => Logger.warn('Immediate api call failed', err))
    }
  }, [immediate, execute])

  // 组件卸载时清理
  useEffect(() => {
    mountedRef.current = true
    return () => {
      mountedRef.current = false
      cancelRef.current = true
    }
  }, [])

  return {
    data,
    loading,
    error,
    execute,
    reset,
    cancel
  }
}

// 用于分页数据的特殊hook
interface UsePaginatedApiOptions<T> extends UseApiOptions<T> {
  initialPage?: number
  initialPageSize?: number
}

interface UsePaginatedApiResult<T, P extends unknown[] = unknown[]> extends UseApiResult<T, P> {
  page: number
  pageSize: number
  total: number
  setPage: (page: number) => void
  setPageSize: (pageSize: number) => void
  refresh: () => void
  loadMore: () => void
}

export function usePaginatedApi<T, P extends unknown[] = unknown[]>(
  apiFunction: (...params: P) => Promise<{ data: T; total: number; page: number; size: number }>,
  options: UsePaginatedApiOptions<T> = {}
): UsePaginatedApiResult<T, P> {
  const { initialPage = 1, initialPageSize = 10, ...apiOptions } = options
  
  const [page, setPage] = useState(initialPage)
  const [pageSize, setPageSize] = useState(initialPageSize)
  const [total, setTotal] = useState(0)
  
  const lastParamsRef = useRef<P | null>(null)

  const apiResult = useApi(
    async (...params: P) => {
      const result = await apiFunction(...params)
      setTotal(result.total)
      return result.data
    },
    apiOptions
  )

  // 刷新当前页
  const refresh = useCallback(() => {
    if (lastParamsRef.current) {
      apiResult.execute(...lastParamsRef.current)
    }
  }, [apiResult])

  // 加载更多（增加页码）
  const loadMore = useCallback(() => {
    if (lastParamsRef.current) {
      setPage(prev => prev + 1)
    }
  }, [])

  // 包装execute方法以保存参数
  const execute = useCallback(async (...params: P) => {
    lastParamsRef.current = params
    return apiResult.execute(...params)
  }, [apiResult])

  // 页码或页大小变化时重新加载
  useEffect(() => {
    if (lastParamsRef.current) {
      execute(...lastParamsRef.current)
    }
  }, [page, pageSize, execute])

  return {
    ...apiResult,
    execute,
    page,
    pageSize,
    total,
    setPage,
    setPageSize,
    refresh,
    loadMore
  }
}

// 用于表单提交的特殊hook
interface UseFormApiOptions<T> extends UseApiOptions<T> {
  resetOnSuccess?: boolean
}

export function useFormApi<T, P extends unknown[] = unknown[]>(
  apiFunction: (...params: P) => Promise<T>,
  options: UseFormApiOptions<T> = {}
): UseApiResult<T, P> & { submit: (...params: P) => Promise<T> } {
  const { resetOnSuccess = false, ...apiOptions } = options
  
  const apiResult = useApi(apiFunction, {
    ...apiOptions,
    onSuccess: (data) => {
      if (resetOnSuccess) {
        apiResult.reset()
      }
      apiOptions.onSuccess?.(data)
    }
  })

  const submit = apiResult.execute

  return {
    ...apiResult,
    submit
  }
}

// 用于文件上传的特殊hook
interface UseUploadApiOptions<T> extends UseApiOptions<T> {
  onProgress?: (percent: number) => void
}

export function useUploadApi<T>(
  uploadFunction: (file: File, onProgress?: (percent: number) => void) => Promise<T>,
  options: UseUploadApiOptions<T> = {}
): UseApiResult<T, [File]> & { 
  progress: number
  upload: (file: File) => Promise<T>
} {
  const { onProgress, ...apiOptions } = options
  const [progress, setProgress] = useState(0)

  const apiResult = useApi(
    async (file: File) => {
      setProgress(0)
      return uploadFunction(file, (percent) => {
        setProgress(percent)
        onProgress?.(percent)
      })
    },
    {
      ...apiOptions,
      onSuccess: (data) => {
        setProgress(100)
        apiOptions.onSuccess?.(data)
      },
      onError: (error) => {
        setProgress(0)
        apiOptions.onError?.(error)
      }
    }
  )

  const upload = apiResult.execute

  return {
    ...apiResult,
    progress,
    upload
  }
}

export default useApi
