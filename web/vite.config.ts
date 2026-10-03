import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'

// In production the gateway (python -m nexgen run, port 8100) serves the built
// app and the API from one origin, so every API call is a same-origin path and
// nothing here needs configuring. In development this server (port 5200)
// proxies /api to the gateway so it still looks like one origin.
const gatewayPort = process.env.NEXGEN_GATEWAY_PORT || '8100'
const target = `http://127.0.0.1:${gatewayPort}`

export default defineConfig({
  plugins: [react(), tailwindcss()],
  define: {
    // The fleet-analytics module's axios clients take their base URL from
    // these; both are the gateway's /api/v1 (ML answers under /api/v1/ml).
    'import.meta.env.VITE_API_URL': JSON.stringify('/api/v1'),
    'import.meta.env.VITE_ML_URL': JSON.stringify('/api/v1'),
  },
  server: {
    port: Number(process.env.NEXGEN_WEB_PORT || 5200),
    strictPort: true,
    proxy: { '/api': target },
  },
  build: {
    chunkSizeWarningLimit: 1200,
  },
})
