export const walletPattern = /^0x[0-9a-f]{40}$/i
export const walletAddress = value => typeof value === 'string' && walletPattern.test(value.trim()) ? value.trim().toLowerCase() : ''
export const shortWallet = address => `${address.slice(0, 8)}…${address.slice(-5)}`
export const caseStatuses = { open: 'Open', review: 'In review', closed: 'Closed' }
export const evidenceSources = {
  simulated: 'Dataset replay', live: 'Confirmed chain', pending: 'Pending observation',
  api: 'API prediction', screening: 'Transaction screening', unknown: 'Unspecified source',
}

export function snapshotDecision(decision, source, capturedAt = new Date().toISOString()) {
  const { fraud_probability: probability, risk_action: action } = decision
  const trust = decision.trust_score ?? decision.projected_trust_score
  return {
    id: String(decision.id ?? JSON.stringify([source, decision.sequence, decision.transaction_hash,
      decision.block_number, decision.model_version, probability, trust, action, decision.recipient, decision.value_eth])),
    source: Object.hasOwn(evidenceSources, source) ? source : 'unknown',
    capturedAt,
    observedAt: Number.isFinite(decision.timestamp) ? decision.timestamp : null,
    probability: Number.isFinite(probability) && probability >= 0 && probability <= 1 ? probability : null,
    trust: Number.isFinite(trust) && trust >= 0 && trust <= 100 ? trust : null,
    action: typeof action === 'string' ? action : null,
    modelVersion: typeof decision.model_version === 'string' ? decision.model_version : null,
    confidence: typeof decision.confidence === 'string' ? decision.confidence : null,
    transactionHash: /^0x[0-9a-f]{64}$/i.test(decision.transaction_hash) ? decision.transaction_hash : null,
    block: Number.isSafeInteger(decision.block_number) ? decision.block_number : null,
    recipient: walletAddress(decision.recipient) || null,
    valueEth: /^(?:\d+\.?\d*|\.\d+)(?:e[+-]?\d+)?$/i.test(decision.value_eth) ? String(decision.value_eth) : null,
    reasons: Array.isArray(decision.explanation) ? decision.explanation.slice(0, 12).map(reason => ({
      feature: String(reason.feature), impact: Number.isFinite(reason.impact) ? reason.impact : null,
    })) : [],
  }
}

export function changedContributions(before, after) {
  const original = new Map(before.map(item => [item.feature, item.impact]))
  return after.flatMap(item => {
    const previous = original.get(item.feature)
    return Number.isFinite(previous) && Number.isFinite(item.impact) && previous !== item.impact
      ? [{ feature: item.feature, delta: item.impact - previous }] : []
  }).sort((a, b) => Math.abs(b.delta) - Math.abs(a.delta)).slice(0, 3)
}

// The graph represents directed wallet relationships, not transaction lineage.
export function findTransferPath(edges, start, end, maxHops = 4) {
  if (!start || !end || start === end) return []
  const outgoing = new Map()
  for (const edge of edges || []) {
    if (edge.transfer_count > 0) {
      if (!outgoing.has(edge.sender)) outgoing.set(edge.sender, [])
      outgoing.get(edge.sender).push(edge)
    }
  }
  const queue = [{ wallet: start, path: [] }]
  const seen = new Set([start])
  for (let i = 0; i < queue.length; i += 1) {
    const { wallet, path } = queue[i]
    if (path.length === maxHops) continue
    for (const edge of outgoing.get(wallet) || []) {
      if (seen.has(edge.recipient)) continue
      const next = [...path, edge]
      if (edge.recipient === end) return next
      seen.add(edge.recipient)
      queue.push({ wallet: edge.recipient, path: next })
    }
  }
  return []
}
