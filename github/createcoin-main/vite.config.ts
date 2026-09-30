import type { ProxyOptions } from 'vite';
import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';
import { nodePolyfills } from 'vite-plugin-node-polyfills';

/** Local dev: mirror Vercel /api/pump/* routes to Pump upstreams. */
const ADV = 'https://advanced-api-v2.pump.fun';
const V3 = 'https://frontend-api-v3.pump.fun';

const pumpApiDevProxy: Record<string, string | ProxyOptions> = {
  '^/api/pump/coins/list': {
    target: ADV,
    changeOrigin: true,
    secure: true,
    rewrite: (path) => path.replace(/^\/api\/pump\/coins\/list/, '/coins/list'),
  },
  '^/api/pump/coins/metadata/': {
    target: ADV,
    changeOrigin: true,
    secure: true,
    rewrite: (path) => path.replace(/^\/api\/pump\/coins/, ''),
  },
  '^/api/pump/coins/search': {
    target: V3,
    changeOrigin: true,
    secure: true,
    rewrite: (path) => path.replace(/^\/api\/pump\/coins\/search/, '/coins/search'),
  },
  '^/api/pump/coins/(?!list|search|metadata)([^/]+)': {
    target: V3,
    changeOrigin: true,
    secure: true,
    rewrite: (path) => path.replace(/^\/api\/pump\/coins\//, '/coins/'),
  },
};

export default defineConfig({
  plugins: [
    react(),
    nodePolyfills({
      globals: { Buffer: true, process: true },
      protocolImports: true,
    }),
  ],
  server: { proxy: pumpApiDevProxy },
  preview: { proxy: pumpApiDevProxy },
  optimizeDeps: {
    exclude: ['lucide-react'],
  },
});
