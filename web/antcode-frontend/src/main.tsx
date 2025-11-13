import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import { ConfigProvider } from 'antd'
import zhCN from 'antd/locale/zh_CN'
import App from './App.tsx'
import { APP_TITLE } from '@/config/app'
import 'dayjs/locale/zh-cn'

// 设置页面标题
if (typeof document !== 'undefined') {
  document.title = APP_TITLE
}

// 性能优化：预连接到 API 服务器
const apiDomain = import.meta.env.VITE_API_BASE_URL
if (apiDomain && typeof document !== 'undefined') {
  const link = document.createElement('link')
  link.rel = 'preconnect'
  link.href = apiDomain
  document.head.appendChild(link)
}

// Ant Design 主题配置
const antdTheme = {
  token: {
    // 品牌色
    colorPrimary: '#1890ff',
    // 圆角
    borderRadius: 6,
    // 字体
    fontFamily: '-apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif',
  },
  components: {
    // 性能优化：减少动画时长
    Motion: {
      motionDurationFast: '0.1s',
      motionDurationMid: '0.2s',
      motionDurationSlow: '0.3s',
    },
  },
}

const root = document.getElementById('root')
if (!root) {
  throw new Error('Root element not found')
}

createRoot(root).render(
  <StrictMode>
    <ConfigProvider 
      locale={zhCN}
      theme={antdTheme}
    >
      <App />
    </ConfigProvider>
  </StrictMode>,
)
