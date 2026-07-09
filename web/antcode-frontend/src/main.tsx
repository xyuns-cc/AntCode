import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import { QueryClientProvider } from '@tanstack/react-query'
import App from './App.tsx'
import { APP_TITLE } from '@/config/app'
import { queryClient } from '@/lib/queryClient'
import { API_BASE_URL } from '@/utils/constants'
import 'dayjs/locale/zh-cn'

document.title = APP_TITLE

// Preconnect to API server
const link = document.createElement('link')
link.rel = 'preconnect'
link.href = API_BASE_URL
document.head.appendChild(link)

const root = document.getElementById('root')
if (!root) throw new Error('Root element not found')

createRoot(root).render(
  <StrictMode>
    <QueryClientProvider client={queryClient}>
      <App />
    </QueryClientProvider>
  </StrictMode>
)
