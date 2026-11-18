import { defineConfig } from '@playwright/test'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { fileURLToPath } from 'node:url'

const root = fileURLToPath(new URL('../', import.meta.url))
const frontendPort = '15173'
const backendPort = '18000'

export default defineConfig({
  testDir: './smoke',
  use: { baseURL: `http://127.0.0.1:${frontendPort}`, browserName: 'chromium' },
  webServer: {
    cwd: root,
    command: 'python3 dev.py',
    wait: { stdout: /Ready: .*\/health\s+Frontend:/ },
    timeout: 120000,
    reuseExistingServer: false,
    gracefulShutdown: { signal: 'SIGINT', timeout: 12000 },
    env: {
      DWTS_FRONTEND_PORT: frontendPort, DWTS_BACKEND_PORT: backendPort,
      DATABASE_PATH: join(tmpdir(), `dwts-stack-${process.pid}-${Date.now()}.sqlite`),
      DATABASE_URL: '', ALLOWED_ORIGINS: '',
      API_KEY: '', VIEWER_API_KEY: '', REVIEWER_API_KEY: '', ADMIN_API_KEY: '',
      WEB3_PROVIDER_URL: '', WEB3_PROVIDER_URLS: '',
      WEB3_WS_PROVIDER_URL: '', WEB3_WS_PROVIDER_URLS: '',
      SSL_CERTFILE: '', SSL_KEYFILE: '', ALERT_WEBHOOK_URL: '', ALERT_SMTP_HOST: '',
    },
  },
})
