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
