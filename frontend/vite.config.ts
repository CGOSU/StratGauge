import { fileURLToPath, URL } from 'node:url'
import { defineConfig } from 'vite'

const backendPort = process.env.STRATGAUGE_BACKEND_PORT ?? '8000'

export default defineConfig({
  resolve: {
    alias: { '@': fileURLToPath(new URL('./src', import.meta.url)) },
  },
  server: {
    port: 5173,
    proxy: {
      '/api': {
        target: `http://127.0.0.1:${backendPort}`,
        rewrite: (path) => path.replace(/^\/api/, ''),
      },
    },
  },
})
