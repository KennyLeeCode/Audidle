import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

/**
 * Dev server proxies /api to the FastAPI backend.
 *
 * Keeping the frontend on a relative /api base means no CORS handling in
 * development and no environment-specific URL compiled into the bundle. In
 * production the same relative path works behind any reverse proxy.
 */
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
})
