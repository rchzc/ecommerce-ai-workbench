import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// 开发态：Vite 在 5173，FastAPI 在 8000，用代理抹平跨域
// 生产态：前端 build 产物由 FastAPI 统一托管，/api 同源，无需代理
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      '/api': {
        target: 'http://127.0.0.1:8000',
        changeOrigin: true,
      },
    },
  },
  build: {
    outDir: 'dist',
    sourcemap: false,
    chunkSizeWarningLimit: 900,
  },
})
