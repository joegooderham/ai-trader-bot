import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

export default defineConfig({
  plugins: [react()],
  server: {
    // Proxy API requests to FastAPI backend during development
    proxy: {
      '/api': {
        target: 'http://localhost:8050',
        changeOrigin: true,
      },
    },
  },
})
