import fs from 'node:fs'
import path from 'node:path'
import { fileURLToPath } from 'node:url'
import react from '@vitejs/plugin-react'
import { defineConfig } from 'vite'

// https://vite.dev/config/
const apiTarget = 'http://127.0.0.1:8000'
const certDir = path.join(path.dirname(fileURLToPath(import.meta.url)), 'certs')

export default defineConfig(() => {
  return {
    server: {
      port: 5181,
      // 开发服务器会把所有接口转发给后端，监听 0.0.0.0 时公网也能访问。
      // 只通过 Tailscale 或本机访问时，设成对应地址，例如 DEV_HOST=100.101.76.39
      host: process.env.DEV_HOST || '0.0.0.0',
      // 麦克风只能在 https 或 localhost 下使用。局域网和 Tailscale 地址走 https。
      https: {
        key: fs.readFileSync(path.join(certDir, 'dev-key.pem')),
        cert: fs.readFileSync(path.join(certDir, 'dev-cert.pem')),
      },
      proxy: {
        // 前端请求走当前页面来源，由开发服务器转到后端。
        // 这样通过局域网或端口转发打开页面时，也不会去请求浏览者自己的 localhost:8000。
        '/asr': {
          target: apiTarget,
          changeOrigin: true,
          ws: true,
        },
        '^/(login|register|sts-token|get_files|delete_file|documents|get_messages|get_sessions|create_session|quick_parse|get_parsed_content|chat_on_docs|upload_files|sessions)(/|\\?|$)':
          {
            target: apiTarget,
            changeOrigin: true,
            timeout: 0,
            proxyTimeout: 0,
            // /login 既是页面路由也是接口。浏览器打开页面时放行，接口请求才转发。
            bypass(req) {
              const accept = req.headers.accept ?? ''
              if (req.method === 'GET' && accept.includes('text/html')) {
                return req.url
              }
            },
          },
      },
    },
    resolve: {
      alias: [
        {
          find: /^@\//,
          replacement: '/src/',
        },
      ],
    },

    plugins: [react()],
  }
})
