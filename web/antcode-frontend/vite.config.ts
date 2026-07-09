import { defineConfig, loadEnv } from 'vite'
import react from '@vitejs/plugin-react'
import path from 'path'
import type { UserConfig } from 'vite'
import viteCompression from 'vite-plugin-compression'
import fs from 'fs'

export default defineConfig(({ mode }) => {
  // 从仓库根或前端目录读取环境变量
  const rootEnvPath = path.resolve(__dirname, '../..')
  const localEnvPath = __dirname
  const envDir = fs.existsSync(path.join(rootEnvPath, '.env')) ? rootEnvPath : localEnvPath
  
  const env = loadEnv(mode, envDir, '')
  const isProduction = mode === 'production'
  
  // API 地址（从环境变量读取，默认 localhost:8000）
  const bindHost = env.BIND_HOST || env.SERVER_DOMAIN || 'localhost'
  const serverPort = env.SERVER_PORT || '8000'
  const apiHost = bindHost === '0.0.0.0' ? 'localhost' : bindHost
  const apiBaseUrl = env.VITE_API_BASE_URL || `http://${apiHost}:${serverPort}`
  // 显式 VITE_WS_BASE_URL 优先；否则从 API base 推导（仅替换协议头，不动 path）。
  const wsBaseUrl = env.VITE_WS_BASE_URL || apiBaseUrl.replace(/^https?:/, (m) => m === 'https:' ? 'wss:' : 'ws:')
  const frontendPort = Number(env.FRONTEND_PORT || '3000')
  
  const config: UserConfig = {
    envDir,
    envPrefix: ['VITE_', 'BIND_', 'SERVER_'],
    plugins: [
      react(),
      // Gzip 压缩（生产环境）
      isProduction && viteCompression({
        verbose: true,
        disable: false,
        threshold: 10240, // 10KB 以上才压缩
        algorithm: 'gzip',
        ext: '.gz',
      }),
    ].filter(Boolean),
    
    resolve: {
      alias: {
        '@': path.resolve(__dirname, './src'),
      },
    },

    server: {
      port: frontendPort,
      host: true,
      hmr: {
        overlay: true,
      },
      proxy: {
        // P14: 前端 WebSocket 端点全部挂在 /api/v1/ws/**（不是 /ws），必须
        // 先声明带 ws:true 的更具体前缀，否则 /api proxy 会先命中把 WS 握手
        // 当普通 HTTP 转发，导致所有实时日志页永远停在 "连接中"。key 顺序
        // 决定 Vite proxy 匹配顺序：从长到短。
        '/api/v1/ws': {
          target: wsBaseUrl,
          ws: true,
          changeOrigin: true,
        },
        '/api': {
          target: apiBaseUrl,
          changeOrigin: true,
          rewrite: (path) => path.replace(/^\/api/, '/api')
        },
        '/ws': {
          target: wsBaseUrl,
          ws: true,
          changeOrigin: true,
          rewrite: (path) => path.replace(/^\/ws/, '/ws')
        },
      },
    },

    build: {
      // 兼容 Chrome 91+/Safari 15+/Edge 91+/Firefox 90+，覆盖 2021 年后大部分主流浏览器
      target: 'es2020',
      minify: 'terser',
      terserOptions: {
        compress: {
          drop_console: mode === 'production',
          drop_debugger: true,
          pure_funcs: mode === 'production' ? ['console.log', 'console.debug'] : [],
        },
        format: {
          comments: false,
        },
      },
      sourcemap: mode !== 'production',
      chunkSizeWarningLimit: 1500,
      rollupOptions: {
        output: {
          manualChunks: (id) => {
            if (!id.includes('node_modules')) return
            if (/[\\/]node_modules[\\/](react|react-dom|react-router-dom|zustand|immer)/.test(id)) {
              return 'core-vendor'
            }
            if (id.includes('@ant-design/icons')) {
              return 'antd-icons'
            }
            if (/[\\/]node_modules[\\/]antd[\\/]es[\\/]locale/.test(id)) {
              return 'antd-locale'
            }
            if (id.includes('antd') || id.includes('@ant-design')) {
              return 'antd-vendor'
            }
            if (id.includes('chart') || id.includes('react-chartjs')) {
              return 'chart-vendor'
            }
            if (id.includes('react-syntax-highlighter') || id.includes('highlight.js') || id.includes('refractor')) {
              return 'syntax-vendor'
            }
            return 'vendor'
          },
          chunkFileNames: 'js/[name]-[hash].js',
          entryFileNames: 'js/[name]-[hash].js',
          assetFileNames: (assetInfo) => {
            const name = assetInfo.name || ''
            const extType = name.split('.').at(-1) || ''
            if (/png|jpe?g|svg|gif|tiff|bmp|ico/i.test(extType)) {
              return 'images/[name]-[hash][extname]'
            }
            if (/woff|woff2|eot|ttf|otf/i.test(extType)) {
              return 'fonts/[name]-[hash][extname]'
            }
            if (extType === 'css') {
              return 'css/[name]-[hash][extname]'
            }
            return 'assets/[name]-[hash][extname]'
          },
        },
      },
      reportCompressedSize: false,
      // 启用 CSS 代码分割
      cssCodeSplit: true,
      // 设置资源内联阈值 (4kb)
      assetsInlineLimit: 4096,
    },

    optimizeDeps: {
      include: [
        'react',
        'react-dom',
        'react-router-dom',
        'antd',
        '@ant-design/icons',
        'axios',
        'zustand',
        'dayjs',
        'immer',
      ],
    },

    // 性能优化
    esbuild: {
      logOverride: { 'this-is-undefined-in-esm': 'silent' },
      drop: mode === 'production' ? ['console', 'debugger'] : [],
    },
  }

  return config
})
