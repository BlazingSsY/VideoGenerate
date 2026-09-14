import { defineConfig, devices } from '@playwright/test'

export default defineConfig({
  testDir: './tests',
  // 外置 exFAT 盘上保存文件会再生 macOS AppleDouble (._*) 垃圾，收集时是二进制 → SyntaxError
  testIgnore: '**/._*',
  timeout: 30000,
  retries: 1,
  use: {
    baseURL: 'http://localhost:5199',
    headless: true,
    screenshot: 'only-on-failure',
    ...(process.env.PLAYWRIGHT_CHANNEL ? { channel: process.env.PLAYWRIGHT_CHANNEL } : {}),
  },
  projects: [
    { name: 'desktop-1920', use: { viewport: { width: 1920, height: 1080 } } },
    { name: 'desktop-1440', use: { viewport: { width: 1440, height: 900 } } },
    { name: 'tablet-768', use: { viewport: { width: 768, height: 1024 } } },
    { name: 'mobile-375', use: { viewport: { width: 375, height: 812 } } },
    { name: 'mobile-320', use: { viewport: { width: 320, height: 568 } } },
  ],
  webServer: {
    // 指向测试栈：前端 dev 固定 5199（--strictPort 不复用其他 vite 实例），
    // 代理到测试后端 8018（需在此前启动）。避免复用用户 5173 的 vite（其代理指向旧后端）。
    command: 'npm run dev -- --port 5199 --strictPort',
    url: 'http://localhost:5199',
    env: { VITE_BACKEND_PROXY_TARGET: 'http://127.0.0.1:8018' },
    reuseExistingServer: true,
    timeout: 30000,
  },
})
