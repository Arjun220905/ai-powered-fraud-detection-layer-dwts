import test from 'node:test'
import assert from 'node:assert/strict'
import { readResponse, formatApiDate } from './api.js'

test('reads successful JSON and empty acknowledgements', async () => {
  assert.deepEqual(await readResponse(new Response('{"ok":true}')), { ok: true })
  assert.equal(await readResponse(new Response(null, { status: 204 })), null)
})

test('makes API validation errors readable', async () => {
  const response = new Response(JSON.stringify({ detail: [{ loc: ['body', 'gas'], msg: 'Must be positive' }] }), { status: 422 })
  await assert.rejects(readResponse(response), /gas: Must be positive/)
})

test('handles non-JSON server errors and malformed success responses', async () => {
  await assert.rejects(readResponse(new Response('Bad gateway', { status: 502 })), /502/)
  await assert.rejects(readResponse(new Response('not JSON')), /unreadable response/)
})

test('treats timezone-free database timestamps as UTC', () => {
  const expected = new Date('2026-09-27T12:00:00Z').toLocaleString()
  assert.equal(formatApiDate('2026-09-27 12:00:00'), expected)
  assert.equal(formatApiDate('2026-09-27T12:00:00'), expected)
  assert.equal(formatApiDate('2026-09-27T17:30:00+05:30'), expected)
  assert.equal(formatApiDate(null), '—')
  assert.equal(formatApiDate('invalid'), 'invalid')
})
