import { useState } from 'react'
import { caseStatuses, changedContributions, evidenceSources, shortWallet, walletAddress } from './investigation'

const risk = value => value == null ? 'Unavailable' : `${(value * 100).toFixed(1)}%`
const formatTime = value => new Date(value).toLocaleString()

export default function CaseDesk({ cases, active, setActive, onFollow, onUpdate, onRemove, onInspect, feed, storageError, feedback }) {
  const [draft, setDraft] = useState('')
  const [filter, setFilter] = useState('all')
  const [query, setQuery] = useState('')
  const [notice, setNotice] = useState('')
  const selected = cases.find(item => item.address === active)
  const needle = query.trim().toLowerCase()
  const visible = cases.filter(item => (filter === 'all' || item.status === filter) &&
    (item.address.includes(needle) || item.label.toLowerCase().includes(needle)))
  const matched = new Set(feed.map(event => walletAddress(event.address)))

  function follow(event) {
    event.preventDefault()
    const address = walletAddress(draft)
    if (!address) { setNotice('Enter a full Ethereum address starting with 0x.'); return }
    setNotice('')
    if (onFollow(address)) { setDraft(''); setFilter('all'); setQuery('') }
  }

  function download() {
    const json = JSON.stringify({
      schemaVersion: 1, network: 'Ethereum', exportedAt: new Date().toISOString(),
      scope: 'Research evidence. Model predictions and investigator notes are not proof of fraud.',
      case: selected,
    }, null, 2)
    const link = document.createElement('a')
    link.href = URL.createObjectURL(new Blob([json], { type: 'application/json' }))
    link.download = `dwts-case-${selected.address}.json`
    link.click()
    setTimeout(() => URL.revokeObjectURL(link.href), 1000)
    setNotice('Case evidence download started.')
  }

  return <section className="case-desk panel" id="case-desk" aria-labelledby="case-desk-heading">
    <header className="desk-heading">
      <div><h2 id="case-desk-heading">Case desk</h2><p>Follow wallets, save assessments, and record your review.</p></div>
      <span>{cases.length} {cases.length === 1 ? 'wallet' : 'wallets'} in this browser</span>
    </header>
    {storageError && <p className="desk-message notice-error" role="alert">{storageError}</p>}
    <div className="desk-layout">
      <div className="desk-watch">
        <h3>Watchlist</h3>
        <form onSubmit={follow} className="desk-follow">
          <label htmlFor="case-wallet">Ethereum address</label>
          <input id="case-wallet" required value={draft} onChange={event => setDraft(event.target.value)} placeholder="0x… full address" maxLength={80} />
          <button type="submit" className="primary-action">Follow wallet</button>
        </form>
        {cases.length > 0 && <>
          <div className="desk-filters">
            <label>Find followed wallet<input value={query} onChange={event => setQuery(event.target.value)} placeholder="Address or label" /></label>
            <label>Case status<select value={filter} onChange={event => setFilter(event.target.value)}><option value="all">All cases</option>{Object.entries(caseStatuses).map(([value, label]) => <option key={value} value={value}>{label}</option>)}</select></label>
          </div>
          <ul className="desk-wallets">{visible.map(item => <li key={item.address}>
            <button type="button" aria-pressed={item.address === active} onClick={() => setActive(item.address)}>
              <strong>{item.label || shortWallet(item.address)}</strong>
              <small>{caseStatuses[item.status]}{matched.has(item.address) ? ' • In current feed' : ''}</small>
            </button>
          </li>)}</ul>
          {!visible.length && <p className="desk-note">No wallets match the current filters.</p>}
        </>}
      </div>
      {selected ? <CaseDetail key={selected.address} item={selected} onUpdate={onUpdate} onRemove={onRemove}
        onInspect={onInspect} onDownload={download} /> : <div className="desk-detail desk-empty">
        <h3>{cases.length ? 'Choose a wallet' : 'Start an investigation'}</h3>
        <p>Follow an address, or save an assessment from a stream, transaction screening, or wallet investigation.</p>
        <a href="#overview">Go to the decision feed</a>
      </div>}
    </div>
    <p className="desk-message" role="status">{notice || feedback}</p>
  </section>
}

function CaseDetail({ item, onUpdate, onRemove, onInspect, onDownload }) {
  const [confirmRemove, setConfirmRemove] = useState(false)
  const [beforeId, setBeforeId] = useState('')
  const [afterId, setAfterId] = useState('')
  const [label, setLabel] = useState(item.label)
  const [notes, setNotes] = useState(item.notes)
  const [status, setStatus] = useState(item.status)
  const [category, setCategory] = useState(item.category)
  const [saved, setSaved] = useState(false)
  const before = item.evidence.find(record => record.id === (beforeId || item.evidence[1]?.id))
  const after = item.evidence.find(record => record.id === (afterId || item.evidence[0]?.id))
  const comparable = before?.id !== after?.id && before?.source === after?.source && before?.source !== 'unknown' &&
    before?.modelVersion && before.modelVersion === after.modelVersion
  const delta = comparable && before.probability != null && after.probability != null
    ? (after.probability - before.probability) * 100 : null
  const contributions = delta == null ? [] : changedContributions(before.reasons, after.reasons)

  function save(event) {
    event.preventDefault()
    onUpdate(item.address, { label: label.trim(), notes: notes.trim(), status, category })
    setSaved(true)
  }

  return <div className="desk-detail">
    <div className="desk-detail-head">
      <div><h3>{item.label || 'Wallet case'}</h3><code>{item.address}</code></div>
      <a href={`https://etherscan.io/address/${item.address}`} target="_blank" rel="noreferrer">View on Etherscan</a>
    </div>
    <div className="desk-actions">
      <button type="button" onClick={() => onInspect(item.address)}>Open wallet history</button>
      <button type="button" onClick={onDownload}>Export case JSON</button>
    </div>
    <form className="desk-review" onSubmit={save} onChange={() => setSaved(false)}>
      <label>Wallet label<input value={label} maxLength={60} onChange={event => setLabel(event.target.value)} /></label>
      <label>Review status<select value={status} onChange={event => setStatus(event.target.value)}>{Object.entries(caseStatuses).map(([value, name]) => <option key={value} value={value}>{name}</option>)}</select></label>
      <label>Review category<select value={category} onChange={event => setCategory(event.target.value)}>{['Uncategorized', 'Suspicious activity', 'Reported scam', 'False-positive review', 'Research sample'].map(name => <option key={name}>{name}</option>)}</select></label>
      <label className="desk-notes">Review notes<textarea rows={3} maxLength={1500} value={notes} onChange={event => setNotes(event.target.value)} placeholder="Record what you observed and what still needs to be checked." /></label>
      <div className="desk-actions"><button className="primary-action">Save case notes</button>{saved && <span role="status">Case notes saved.</span>}</div>
    </form>
    <section className="desk-evidence" aria-label="Saved assessments">
      <h4>Saved assessments <span>{item.evidence.length}</span></h4>
      {!item.evidence.length ? <p className="desk-note">Save an assessment from the decision feed or wallet investigation to add it here.</p> : <>
        <ol>{item.evidence.map(record => <li key={record.id}>
          <div><strong>{evidenceSources[record.source]}</strong><small>Saved {formatTime(record.capturedAt)}{record.block != null ? ` · Block ${record.block}` : ''}</small></div>
          <div><strong>{risk(record.probability)}</strong><small>{record.action || 'No action'}</small></div>
          <details><summary>Evidence</summary><p>Trust {record.trust ?? 'unavailable'} · Model {record.modelVersion ?? 'unavailable'}{record.confidence && ` · ${record.confidence.replaceAll('_', ' ')}`}</p>
            {record.valueEth != null && <p>{record.valueEth} ETH{record.recipient && ` to ${shortWallet(record.recipient)}`}</p>}
            {record.transactionHash && record.source === 'live' && <a href={`https://etherscan.io/tx/${record.transactionHash}`} target="_blank" rel="noreferrer">Open confirmed transaction</a>}
            {record.reasons.length > 0 && <ul>{record.reasons.map((reason, index) => <li key={index}>{reason.feature}: {reason.impact ?? '—'} log-odds</li>)}</ul>}
          </details>
        </li>)}</ol>
        {item.evidence.length > 1 && <div className="desk-compare">
          <h4>Compare assessments</h4>
          <div className="desk-compare-fields">{[[before, setBeforeId, 'Earlier assessment'], [after, setAfterId, 'Comparison assessment']].map(([record, setter, label]) => <label key={label}>{label}<select value={record?.id ?? ''} onChange={event => setter(event.target.value)}>
            {item.evidence.map(entry => <option key={entry.id} value={entry.id}>{evidenceSources[entry.source]} · {risk(entry.probability)} · {formatTime(entry.capturedAt)}</option>)}
          </select><strong>{risk(record?.probability)}</strong><small>Trust {record?.trust ?? 'unavailable'} · Model {record?.modelVersion ?? 'unavailable'}</small></label>)}</div>
          <p>{before?.id === after?.id ? 'Choose two different assessments.' : delta == null ? 'A risk delta requires the same evidence source and a matching model version.' : `Risk change: ${delta > 0 ? '+' : ''}${delta.toFixed(2)} percentage points. Changes reflect model estimates.`}</p>
          {contributions.length > 0 && <div className="desk-driver-delta"><strong>Largest changes among shared model contributions</strong>
            <ul>{contributions.map(item => <li key={item.feature}><span>{item.feature}</span><strong>{item.delta > 0 ? '+' : ''}{item.delta.toFixed(3)} log-odds</strong></li>)}</ul>
          </div>}
        </div>}
      </>}
    </section>
    {confirmRemove ? <div className="desk-actions"><span>Remove this local case and its saved evidence?</span><button type="button" onClick={() => onRemove(item.address)}>Remove case</button><button type="button" onClick={() => setConfirmRemove(false)}>Cancel</button></div>
      : <button type="button" className="desk-remove" onClick={() => setConfirmRemove(true)}>Stop following</button>}
  </div>
}
