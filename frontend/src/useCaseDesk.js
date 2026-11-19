import { useState } from 'react'
import { caseStatuses, snapshotDecision, walletAddress } from './investigation'

const key = 'dwts-case-desk-v1'
const maxCases = 50
const maxAssessments = 20

function readCases() {
  const value = JSON.parse(localStorage.getItem(key) || '[]')
  if (!Array.isArray(value)) throw new Error('Invalid case data')
  return value.filter(item => item && walletAddress(item.address) && walletAddress(item.address) === item.address &&
    Object.hasOwn(caseStatuses, item.status) && ['label', 'notes', 'category'].every(field => typeof item[field] === 'string') &&
    Array.isArray(item.evidence) && item.evidence.every(record => record && typeof record.id === 'string' &&
      typeof record.capturedAt === 'string' && Array.isArray(record.reasons)))
}

export default function useCaseDesk() {
  const [state, setState] = useState(() => {
    try { return { cases: readCases(), error: '' } }
    catch { return { cases: [], error: 'Saved cases could not be read. You can start a new case in this tab.' } }
  })
  const [active, setActive] = useState('')
  const [feedback, setFeedback] = useState('')

  function persist(cases) {
    let error = ''
    try { localStorage.setItem(key, JSON.stringify(cases)) }
    catch { error = 'Browser storage is unavailable. Your changes stay in this tab until it closes. Export a case to keep a copy.' }
    setState({ cases, error })
  }

  function open(address, decision, source) {
    const normalized = walletAddress(address)
    if (!normalized) return false
    const existing = state.cases.find(item => item.address === normalized)
    if (!existing && state.cases.length >= maxCases) {
      setFeedback('The watchlist holds 50 wallets. Remove a case before following another.')
      return false
    }
    const item = existing || { address: normalized, label: '', notes: '', status: 'open', category: 'Uncategorized', evidence: [] }
    if (decision) {
      const record = snapshotDecision(decision, source)
      if (item.evidence.some(entry => entry.id === record.id)) {
        setFeedback('This assessment is already saved in the case.')
      } else if (item.evidence.length >= maxAssessments) {
        setFeedback('This case holds 20 assessments. Export it to keep a copy.')
        return false
      } else {
        persist([{ ...item, evidence: [record, ...item.evidence] }, ...state.cases.filter(entry => entry.address !== normalized)])
        setFeedback('Assessment saved in the Case desk.')
      }
    } else if (!existing) {
      persist([item, ...state.cases])
      setFeedback('Wallet followed in the Case desk.')
    } else {
      setFeedback('Opened the existing wallet case.')
    }
    setActive(normalized)
    return true
  }

  return { cases: state.cases, storageError: state.error, active, setActive, feedback, open,
    onUpdate: (address, changes) => persist(state.cases.map(item => item.address === address ? { ...item, ...changes } : item)),
    onRemove: address => {
      persist(state.cases.filter(item => item.address !== address))
      setActive('')
      setFeedback('Local case removed.')
    },
  }
}
