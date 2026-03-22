import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import path from 'path'

export default defineConfig({
  plugins: [react()],
  resolve: {
    alias: { '@': path.resolve(__dirname, './src') },
  },
  // Dev server — Tauri will open this window during development
  server: {
    port: 5173,
    strictPort: true,
    host: '127.0.0.1',
    proxy: {
      '/api': {
        target: 'http://127.0.0.1:8765',
        changeOrigin: true,
      },
      '/ws': {
        target: 'ws://127.0.0.1:8765',
        ws: true,
      },
      '/health': {
        target: 'http://127.0.0.1:8765',
        changeOrigin: true,
      },
    },
  },
  // Production build output goes to dist/ pointed at by tauri.conf.json
  build: {
    outDir: 'dist',
    emptyOutDir: true,
  },

})
