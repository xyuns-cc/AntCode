import { defineConfig, loadEnv } from 'vite'
import react from '@vitejs/plugin-react'
import path from 'path'
import type { UserConfig } from 'vite'
import viteCompression from 'vite-plugin-compression'
import fs from 'fs'

export default defineConfig(({ mode }) => {
  // 尝试从多个位置读取环境变量（兼容 Docker 构建）
  const rootEnvPath = path.resolve(__dirname, '../..')
  const localEnvPath = __dirname
  const envDir = fs.existsSync(path.join(rootEnvPath, '.env')) ? rootEnvPath : localEnvPath
  
  const env = loadEnv(mode, envDir, '')
  const isProduction = mode === 'production'
  
  // API 地址（从环境变量读取，默认 localhost:8000）
  const serverHost = env.SERVER_HOST || env.SERVER_DOMAIN || 'localhost'
  const serverPort = env.SERVER_PORT || '8000'
  const apiBaseUrl = env.VITE_API_BASE_URL || `http://${serverHost === '0.0.0.0' ? 'localhost' : serverHost}:${serverPort}`
  const wsBaseUrl = apiBaseUrl.replace(/^http/, 'ws')
  const frontendPort = Number(env.FRONTEND_PORT || '3000')
  
  const config: UserConfig = {
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
      target: 'es2022',
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
            if (id.includes('node_modules')) {
              if (/[\\/]node_modules[\\/](react|react-dom|react-router-dom|zustand|immer)/.test(id)) {
                return 'core-vendor'
              }
              if (id.includes('antd') || id.includes('@ant-design')) {
                return 'antd-vendor'
              }
              if (id.includes('monaco-editor')) {
                return 'monaco-vendor'
              }
              if (id.includes('chart') || id.includes('react-chartjs')) {
                return 'chart-vendor'
              }
              return 'vendor'
            }
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
      exclude: ['@monaco-editor/react', 'monaco-editor'],
    },

    // 性能优化
    esbuild: {
      logOverride: { 'this-is-undefined-in-esm': 'silent' },
      drop: mode === 'production' ? ['console', 'debugger'] : [],
    },
  }

  return config
})
