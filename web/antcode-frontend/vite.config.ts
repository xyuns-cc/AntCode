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
        // 实时日志改用 SSE（GET /api/v1/logs/runs/{id}/stream），普通 HTTP
        // 代理即可覆盖；http-proxy 对流式响应默认逐块转发，无需额外配置。
        '/api': {
          target: apiBaseUrl,
          changeOrigin: true,
          rewrite: (path) => path.replace(/^\/api/, '/api')
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
          manualChunks: {
            core: ['react', 'react-dom', 'react-router-dom', 'zustand', 'immer'],
            antd: ['antd'],
            icons: ['@ant-design/icons'],
            charts: ['chart.js', 'react-chartjs-2', 'chartjs-plugin-zoom'],
            syntax: ['react-syntax-highlighter'],
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
