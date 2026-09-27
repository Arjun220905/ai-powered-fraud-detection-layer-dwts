import { test, expect } from '@playwright/test'

const address = index => `0x${index.toString(16).padStart(40, '0')}`
const decision = { id: 'example', address: address(1), sequence: 1, source: 'Example wallet', timestamp: 1000, fraud_probability: .9, trust_score: 30, risk_action: 'Verify', explanation: [{ feature: 'Transfer frequency', impact: .4, direction: 'higher risk' }] }
const graph = { nodes: Array.from({ length: 30 }, (_, i) => ({ address: address(i + 1), probability: i % 2 ? .2 : .9 })), edges: [{ sender: address(1), recipient: address(2), count: 2, eth_volume: 1 }] }
const overview = { observations: 1, chain_events: 1, graph, trends: [], clusters: [], anomalies: [decision], monitoring: { mean_ms: 2, p95_ms: 2, latency_samples: 1, drift_warnings: 0, distribution: [] } }

test.beforeEach(async ({ page }) => {
  await page.route('**/api/**', async route => {
    const url = new URL(route.request().url())
    const path = url.pathname
    let body = {}
    if (path.endsWith('/overview')) body = overview
    else if (path.endsWith('/alerts')) body = []
    else if (path.endsWith('/status')) body = { configured: false, connected: false, pending: { configured: false } }
    else if (path.endsWith('/stream/next')) body = decision
    else if (path.endsWith('/screen-transaction')) body = { ...decision, projected_trust_score: 30, observed_events: 10 }
    else if (path.endsWith('/simulate')) body = { baseline: { fraud_probability: .2, model_probability: .2, projected_trust_score: 72 }, scenario: { fraud_probability: .35, model_probability: .2, projected_trust_score: 68 }, probability_delta: .15, trust_delta: -4, adjustments: { amount: .1, recipient: .05 } }
    else if (path.endsWith('/history')) {
      const current = Number(url.searchParams.get('page'))
      body = { items: [{ kind: 'decision', timestamp: 1000, data: { ...decision, id: `page-${current}` } }], page: current, page_size: 50, total: 75, has_more: current === 1, until: 2000 }
    } else if (path.includes('/intelligence/wallet/')) body = { address: address(1), profile: { events: 1, counterparties: [address(2)], receipt_samples: 1 }, timeline: [], scope: 'Test evidence' }
    await route.fulfill({ json: body, headers: { 'Access-Control-Allow-Origin': '*', 'Access-Control-Allow-Headers': 'Content-Type, X-API-Key', 'Access-Control-Allow-Methods': 'GET, POST, OPTIONS' } })
  })
  await page.goto('/')
  await expect(page.getByRole('heading', { name: 'Understand a wallet’s risk' })).toBeVisible()
})

for (const width of [320, 390, 768, 1024, 1440]) {
  test(`no horizontal overflow at ${width}px`, async ({ page }) => {
    await page.setViewportSize({ width, height: 900 })
    await expect(page.getByRole('heading', { name: 'Wallet transaction graph' })).toBeVisible()
    const size = await page.evaluate(() => ({ viewport: innerWidth, page: document.documentElement.scrollWidth }))
    expect(size.page).toBeLessThanOrEqual(size.viewport)
  })
}

test('desktop panels share aligned tops, widths and bottoms', async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 1000 })
  await expect(page.getByRole('heading', { name: 'Wallet transaction graph' })).toBeVisible()
  for (const selector of ['.monitor-grid', '.work-grid', '.intel-grid']) {
    const boxes = await page.locator(`${selector} > .panel`).evaluateAll(nodes => nodes.map(node => { const r = node.getBoundingClientRect(); return { top: r.top, bottom: r.bottom, width: r.width } }))
    for (let i = 0; i + 1 < boxes.length; i += 2) {
      expect(Math.abs(boxes[i].top - boxes[i + 1].top)).toBeLessThan(2)
      expect(Math.abs(boxes[i].bottom - boxes[i + 1].bottom)).toBeLessThan(2)
      expect(Math.abs(boxes[i].width - boxes[i + 1].width)).toBeLessThan(2)
    }
  }
})

test('graph search, filter and view controls align consistently', async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 1000 })
  const controls = await Promise.all([
    page.getByLabel('Find wallet', { exact: true }).boundingBox(),
    page.getByRole('button', { name: 'Search graph', exact: true }).boundingBox(),
    page.getByLabel('Show wallets', { exact: true }).boundingBox(),
    page.getByRole('button', { name: 'Zoom in', exact: true }).boundingBox(),
  ])
  expect(controls.every(Boolean)).toBe(true)
  expect(Math.abs(controls[0].y - controls[1].y)).toBeLessThan(2)
  expect(Math.abs(controls[0].height - controls[1].height)).toBeLessThan(2)
  expect(Math.abs(controls[2].height - controls[3].height)).toBeLessThan(2)
  expect(Math.abs((controls[2].y + controls[2].height) - (controls[3].y + controls[3].height))).toBeLessThan(2)
})

test('graph filters, pagination and zoom controls work', async ({ page }) => {
  await expect(page.getByRole('button', { name: /^Investigate 0x/ })).toHaveCount(24)
  await page.getByRole('button', { name: 'Next wallets', exact: true }).click()
  await expect(page.getByRole('button', { name: /^Investigate 0x/ })).toHaveCount(6)
  await page.getByLabel('Show wallets', { exact: true }).selectOption('high')
  await expect(page.getByRole('button', { name: /^Investigate 0x/ })).toHaveCount(15)
  await page.getByRole('button', { name: 'Zoom in', exact: true }).click()
  await expect(page.getByText(/Zoom: 150%/)).toBeVisible()
  const canvas = page.getByRole('group', { name: 'Wallet connections; use arrow keys to pan when zoomed', exact: true })
  const beforePan = await canvas.getAttribute('viewBox')
  await canvas.press('ArrowRight')
  await expect(canvas).not.toHaveAttribute('viewBox', beforePan)
  await page.getByRole('button', { name: 'Reset view', exact: true }).click()
  await expect(page.getByRole('button', { name: 'Zoom out', exact: true })).toBeDisabled()
  await page.getByLabel('Find wallet', { exact: true }).fill('not-found')
  await expect(page.getByRole('button', { name: /^Investigate 0x/ })).toHaveCount(15)
  await page.getByRole('button', { name: 'Search graph', exact: true }).click()
  await expect(page.getByText('No matching wallets', { exact: true })).toBeVisible()
})

test('history pages preserve snapshot and expose older evidence', async ({ page }) => {
  await page.getByLabel('Full wallet address for investigation').fill(address(1))
  await page.getByRole('button', { name: 'Open investigation', exact: true }).click()
  await expect(page.getByText('75 records · page 1 of 2')).toBeVisible()
  const request = page.waitForRequest(r => r.url().includes('/history?page=2') && r.url().includes('until=2000'))
  await page.getByRole('button', { name: 'Older history', exact: true }).click()
  await request
  await expect(page.getByText('75 records · page 2 of 2')).toBeVisible()
  await expect(page.getByRole('button', { name: 'Older history', exact: true })).toBeDisabled()
})

test('editing a screening form clears the previous result', async ({ page }) => {
  await page.getByRole('textbox', { name: 'Sender wallet Required', exact: true }).fill(address(1))
  await page.getByRole('button', { name: 'Run screening', exact: true }).click()
  await expect(page.getByText('Screening result', { exact: true })).toBeVisible()
  await page.getByRole('spinbutton', { name: 'Value ETH', exact: true }).fill('2')
  await expect(page.getByText('Screening result', { exact: true })).toHaveCount(0)
})

test('API errors are actionable and do not crash the page', async ({ page }) => {
  await page.route('**/api/screen-transaction', route => route.fulfill({ status: 422, headers: { 'Access-Control-Allow-Origin': '*' }, json: { detail: [{ loc: ['body', 'gas'], msg: 'Must be at least 21000' }] } }))
  await page.getByRole('textbox', { name: 'Sender wallet Required', exact: true }).fill(address(1))
  await page.getByRole('button', { name: 'Run screening', exact: true }).click()
  await expect(page.getByRole('status')).toContainText('gas: Must be at least 21000')
})

test('what-if simulator is self-contained and explains its result', async ({ page }) => {
  await page.getByLabel('Simulator sender wallet', { exact: true }).fill(address(1))
  await page.getByLabel('Original amount ETH', { exact: true }).fill('1')
  await page.getByLabel('Test amount ETH', { exact: true }).fill('20')
  await page.getByRole('button', { name: 'Compare risk', exact: true }).click()
  const result = page.getByRole('status')
  await expect(result).toContainText('20.0%')
  await expect(result).toContainText('35.0%')
  await expect(result).toContainText('+15.00 percentage points')
  await expect(result).toContainText('Raw wallet model: 20.0% → 20.0%')
  await expect(result).toContainText('amount +10.0 points, recipient +5.0 points')
  await page.getByLabel('Test amount ETH', { exact: true }).fill('1')
  await expect(page.getByRole('button', { name: 'Change an amount or recipient', exact: true })).toBeDisabled()
  await expect(result).toHaveCount(0)
})

test('navigation highlights the selected workspace section', async ({ page }) => {
  const screening = page.getByRole('link', { name: 'Screen transaction', exact: true })
  await screening.click()
  await expect(screening).toHaveAttribute('aria-current', 'location')
  const intelligence = page.getByRole('link', { name: 'Intelligence', exact: true })
  await intelligence.click()
  await expect(intelligence).toHaveAttribute('aria-current', 'location')
})

test('theme toggle switches modes and remembers the choice', async ({ page }) => {
  const darkToggle = page.getByRole('button', { name: 'Switch to dark mode', exact: true })
  await expect(darkToggle).toBeVisible()
  await darkToggle.click()
  await expect(page.locator('html')).toHaveAttribute('data-theme', 'dark')
  expect(await page.evaluate(() => localStorage.getItem('dwts-theme'))).toBe('dark')
  await page.reload()
  await expect(page.locator('html')).toHaveAttribute('data-theme', 'dark')
  await page.getByRole('button', { name: 'Switch to light mode', exact: true }).click()
  await expect(page.locator('html')).toHaveAttribute('data-theme', 'light')
})
