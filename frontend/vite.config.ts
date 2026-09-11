import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

export default defineConfig({
  plugins: [react()],
  build: {
    // 构建产物与前端源码归在同一模块下，避免污染仓库根目录。
    outDir: 'dist',
    emptyOutDir: true,
  },
  server: {
    port: 5173,
    // Dockerized frontend dev server needs the host gateway; local Vite keeps localhost.
    host: '0.0.0.0',
    proxy: {
      '/api': process.env.VITE_BACKEND_PROXY_TARGET || 'http://127.0.0.1:8008',
      '/media': process.env.VITE_BACKEND_PROXY_TARGET || 'http://127.0.0.1:8008',
    },
  },
})
