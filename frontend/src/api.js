export async function readResponse(response) {
  if (response.status === 204) return null
  const body = await response.json().catch(() => null)
  if (!response.ok) {
    const detail = body?.detail
    const message = typeof detail === 'string' ? detail : Array.isArray(detail)
      ? detail.map(item => `${(item.loc || []).filter(part => part !== 'body').join(' / ')}: ${item.msg}`).join('; ')
      : `Request failed (${response.status}). Check the API connection and try again.`
    throw new Error(message)
  }
  if (body === null) throw new Error('The API returned an unreadable response. Please retry.')
  return body
}

export function formatApiDate(value) {
  if (!value) return '—'
  // SQLite returns UTC timestamps without a timezone suffix.
  const normalized = /^\d{4}-\d\d-\d\d[T ]\d\d:\d\d:\d\d(?:\.\d+)?$/.test(value) ? `${value.replace(' ', 'T')}Z` : value
  const date = new Date(normalized)
  return Number.isNaN(date.getTime()) ? String(value) : date.toLocaleString()
}

export function blockAge(timestamp, now = Date.now()) {
  const seconds = Math.max(0, Math.floor(now / 1000 - timestamp))
  if (seconds < 60) return 'Less than a minute ago'
  const [unit, size] = seconds >= 86400 ? ['day', 86400] : seconds >= 3600 ? ['hour', 3600] : ['minute', 60]
  return new Intl.RelativeTimeFormat(undefined, { numeric: 'always' }).format(-Math.floor(seconds / size), unit)
}
