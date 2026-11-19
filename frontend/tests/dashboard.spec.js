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

test('confirmed-chain health reports an ingestion failure even with a connected provider', async ({ page }) => {
  await page.route('**/api/blockchain/status', route => route.fulfill({
    headers: { 'Access-Control-Allow-Origin': '*' },
    json: {
      configured: true, connected: true, latest_block: 120,
      ingestion: { last_confirmed_block: 100, last_confirmed_timestamp: 1700000000, blocks_behind: 18, error: 'Provider request timed out' },
    },
  }))
  await page.reload()
  const health = page.getByRole('region', { name: 'Confirmed chain health' })
  await expect(health.getByText('Ingestion degraded')).toBeVisible()
  await expect(health.getByText('18 blocks behind', { exact: true })).toBeVisible()
  await health.getByText('Block details', { exact: true }).click()
  await expect(health.getByText('120', { exact: true })).toBeVisible()
  await expect(health.getByText('Provider request timed out')).toBeVisible()
})

test('case notes, saved assessments, and export survive a reload', async ({ page }) => {
  const desk = page.getByRole('region', { name: 'Case desk', exact: true })
  await page.getByRole('link', { name: 'Case desk', exact: true }).click()
  await desk.getByLabel('Ethereum address').fill(address(1))
  await desk.getByRole('button', { name: 'Follow wallet', exact: true }).click()
  await desk.getByLabel('Wallet label').fill('Course demonstration')
  await desk.getByLabel('Review notes').fill('Historical observation for review.')
  await desk.getByLabel('Review status').selectOption('review')
  await desk.getByRole('button', { name: 'Save case notes' }).click()
  await page.getByRole('button', { name: 'Start Dataset replay' }).click()
  await page.getByRole('button', { name: 'Save current assessment' }).click()
  await expect(desk.getByRole('region', { name: 'Saved assessments' }).getByText('Dataset replay')).toBeVisible()
  await page.getByRole('button', { name: 'Pause Dataset replay' }).click()
  await page.reload()
  await desk.getByRole('button', { name: /Course demonstration.*In review/ }).click()
  await expect(desk.getByLabel('Review notes')).toHaveValue('Historical observation for review.')
  const [download] = await Promise.all([page.waitForEvent('download'), desk.getByRole('button', { name: 'Export case JSON' }).click()])
  expect(download.suggestedFilename()).toBe(`dwts-case-${address(1)}.json`)
  await desk.getByRole('button', { name: 'Open wallet history' }).click()
  await expect(page.getByLabel('Full wallet address for investigation')).toHaveValue(address(1))
})

test('trace finds an observed two-hop path and respects transfer filters and direction', async ({ page }) => {
  await page.route('**/api/intelligence/overview?source=live', route => route.fulfill({
    headers: { 'Access-Control-Allow-Origin': '*' },
    json: { ...overview, graph: { ...graph, edges: [
      { sender: address(1), recipient: address(2), transfer_count: 2, native_transfers: 2, token_transfers: 0, unknown_receipts: 1 },
      { sender: address(2), recipient: address(3), transfer_count: 1, native_transfers: 1, token_transfers: 0, unknown_receipts: 0 },
      { sender: address(1), recipient: address(3), transfer_count: 0, native_transfers: 0, token_transfers: 0 },
    ] } },
  }))
  const trace = page.getByRole('region', { name: 'Trace wallet connections' })
  await trace.getByRole('button', { name: 'Use retained confirmed chain' }).click()
  await trace.getByLabel('Starting wallet').fill(address(1))
  await trace.getByLabel('Destination wallet').fill(address(3))
  await trace.getByRole('button', { name: 'Find transfer path' }).click()
  await expect(trace.getByText('Shortest observed path: 2 hops.')).toBeVisible()
  await expect(trace.getByText(/1 with unknown receipt status/)).toBeVisible()
  await trace.getByLabel('Transfer type').selectOption('tokens')
  await expect(trace.getByText('No directed path within four hops in this sample.')).toBeVisible()
  await trace.getByLabel('Transfer type').selectOption('all')
  await trace.getByRole('button', { name: 'Swap wallets' }).click()
  await trace.getByRole('button', { name: 'Find transfer path' }).click()
  await expect(trace.getByText('No directed path within four hops in this sample.')).toBeVisible()
})

test('two saved screenings compare risk and shared contributions', async ({ page }) => {
  await page.route('**/api/screen-transaction', route => {
    const value = route.request().postDataJSON()?.value_eth ?? 0
    return route.fulfill({ headers: { 'Access-Control-Allow-Origin': '*' }, json: {
      ...decision, id: `screen-${value}`, model_version: 'course-model',
      fraud_probability: value === 1 ? .2 : .5,
      explanation: [{ feature: 'Transfer frequency', impact: value === 1 ? .4 : .8 }],
    } })
  })
  const screening = page.locator('#screening')
  await screening.getByLabel(/Sender wallet/).fill(address(1))
  for (const amount of ['1', '2']) {
    await screening.getByLabel(/Value/).fill(amount)
    await screening.getByRole('button', { name: 'Run screening' }).click()
    await screening.getByRole('button', { name: 'Save screening assessment' }).click()
  }
  const desk = page.getByRole('region', { name: 'Case desk', exact: true })
  await expect(desk.getByText(/Risk change: \+30.00 percentage points/)).toBeVisible()
  await expect(desk.getByText('+0.400 log-odds')).toBeVisible()
  await desk.getByLabel('Comparison assessment').selectOption('screen-1')
  await expect(desk.getByText('Choose two different assessments.')).toBeVisible()
})

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

test('color theme offers only the two Indigo palettes and remembers the choice', async ({ page }) => {
  const picker = page.getByRole('combobox', { name: 'Color theme' })
  await expect(picker.getByRole('option')).toHaveText(['Indigo light', 'Indigo dark'])
  for (const theme of ['light', 'dark']) {
    await picker.selectOption(theme)
    await expect(page.locator('html')).toHaveAttribute('data-theme', theme)
  }
  expect(await page.evaluate(() => localStorage.getItem('dwts-theme'))).toBe('dark')
  await page.reload()
  await expect(page.locator('html')).toHaveAttribute('data-theme', 'dark')
  for (const retired of ['ocean', 'sand']) {
    await page.emulateMedia({ colorScheme: 'light' })
    await page.evaluate(theme => localStorage.setItem('dwts-theme', theme), retired)
    await page.reload()
    await expect(picker).toHaveValue('light')
    await expect(page.locator('html')).toHaveAttribute('data-theme', 'light')
  }
})
