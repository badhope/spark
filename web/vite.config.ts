import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'

const backendTarget = process.env.SPARK_DEV_BACKEND || 'http://127.0.0.1:8000'
const backendOrigin = new URL(backendTarget).origin

export default defineConfig({
  plugins: [react(), tailwindcss(), {
    name: 'validate-dev-api-origin',
    configureServer(server) {
      server.middlewares.use('/api', (req, res, next) => {
        const origin = req.headers.origin
        if (req.headers['sec-fetch-site'] === 'cross-site' || (origin && origin !== `http://${req.headers.host}`)) {
          res.statusCode = 403
          res.end('cross-origin request rejected')
          return
        }
        next()
      })
    },
  }],
  build: {
    outDir: '../src/spark/web/dist',
    emptyOutDir: true,
    rollupOptions: {
      output: {
        manualChunks(id) {
          if (id.includes('node_modules/react/') || id.includes('node_modules/react-dom/') || id.includes('node_modules/scheduler/')) return 'react'
          if (id.includes('node_modules/react-markdown/') || id.includes('node_modules/remark-gfm/')) return 'markdown'
          if (
            id.includes('node_modules/highlight.js/lib/core') ||
            id.includes('node_modules/highlight.js/lib/common') ||
            ['bash', 'python', 'typescript', 'javascript', 'json'].some((language) => id.includes(`node_modules/highlight.js/lib/languages/${language}`))
          ) return 'highlight'
        },
      },
    },
  },
  server: {
    // Loopback only. The backend grants token-free control to requests arriving
    // from 127.0.0.1, and the proxy makes every dev request look local — binding
    // 0.0.0.0 would hand the whole agent to anyone on the LAN.
    // Phone/laptop testing on the same network: `spark web --host 0.0.0.0`
    // and open the backend port directly (it enforces X-Spark-Token).
    host: '127.0.0.1',
    port: 5173,
    strictPort: false,
    proxy: {
      '/api': {
        target: backendTarget,
        changeOrigin: true,
        configure(proxy) {
          proxy.on('proxyReq', (proxyReq, req) => {
            if (req.headers.origin) proxyReq.setHeader('origin', backendOrigin)
          })
        },
      },
    },
  },
})
