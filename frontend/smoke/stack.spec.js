import { test, expect } from '@playwright/test'

test('the launcher serves a real replay decision and isolated trust history', async ({ page }) => {
  await page.goto('/')
  await expect(page.getByRole('heading', { name: 'Understand a wallet’s risk' })).toBeVisible()
  const health = page.getByRole('region', { name: 'Confirmed chain health' })
  await expect(health.getByText('Not configured')).toBeVisible()
  const decision = page.waitForResponse(response =>
    response.url().endsWith('/api/stream/next') && response.request().method() === 'POST')
  await page.getByRole('button', { name: 'Start Dataset replay' }).click()
  const response = await decision
  expect(response.ok()).toBe(true)
  const payload = await response.json()
  expect(payload.explanation.length).toBeGreaterThan(0)
  const walletUrl = new URL(`/api/wallets/${payload.address}?page=1`, response.url())
  expect((await page.request.get(walletUrl.href)).status()).toBe(404)
  await expect(page.getByRole('button', { name: 'Pause Dataset replay' })).toBeVisible()
  await expect(page.getByText(/\d+ events in view/)).toBeVisible()
  await page.getByRole('button', { name: 'Pause Dataset replay' }).click()
  await page.getByRole('button', { name: 'Save current assessment' }).click()
  const desk = page.getByRole('region', { name: 'Case desk', exact: true })
  await expect(desk.getByRole('region', { name: 'Saved assessments' }).getByText('Dataset replay')).toBeVisible()
  await expect(desk.getByText(payload.address, { exact: true })).toBeVisible()
})
