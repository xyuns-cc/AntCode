import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import App from './App.tsx'
import { APP_TITLE } from '@/config/app'
import 'dayjs/locale/zh-cn'

document.title = APP_TITLE

// Preconnect to API server
const apiDomain = import.meta.env.VITE_API_BASE_URL
if (apiDomain) {
  const link = document.createElement('link')
  link.rel = 'preconnect'
  link.href = apiDomain
  document.head.appendChild(link)
}

const root = document.getElementById('root')
if (!root) throw new Error('Root element not found')

createRoot(root).render(
  <StrictMode>
    <App />
  </StrictMode>
)
