import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'
import { fileURLToPath, URL } from 'url'

// https://vite.dev/config/
export default defineConfig({
  plugins: [vue()],
  resolve: {
    alias: {
      '@': fileURLToPath(new URL('./src', import.meta.url)),
    },
  },
  server: {
    host: '127.0.0.1',
    proxy: {
      '/api/': {
        target: 'http://localhost:8000', // 您的后端 API 地址
        changeOrigin: true,
      },
      '/media': {
        target: 'http://localhost:8000', // 您的后端 API 地址
        changeOrigin: true,
      },
    },
  },
})
