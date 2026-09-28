import { lazy, Suspense, useEffect, useRef, useState } from 'react'
import { Area, AreaChart, CartesianGrid, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts'
const Intelligence = lazy(() => import('./Intelligence'))
import { readResponse, formatApiDate } from './api'

const API = import.meta.env.VITE_API_URL || 'http://localhost:8000'
const MODES = {
  simulated: { label: 'Dataset replay', note: 'Historical labelled wallets', interval: 1200 },
  pending: { label: 'Pending watch', note: 'Provider-visible mempool', interval: 2000 },
  live: { label: 'Confirmed chain', note: 'Ethereum Mainnet blocks', interval: 5000 },
}

function initialTheme() {
  try {
    const saved = localStorage.getItem('dwts-theme')
    if (saved === 'light' || saved === 'dark') return saved
    return window.matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light'
  } catch { return 'light' }
}

export default function App() {
  const [theme, setTheme] = useState(initialTheme)
  const [activeSection, setActiveSection] = useState('overview')
  const [scorePage, setScorePage] = useState(0)
  const [mode, setMode] = useState('simulated')
  const [feed, setFeed] = useState([])
  const [running, setRunning] = useState(false)
  const [error, setError] = useState('')
  const [block, setBlock] = useState(null)
  const [apiKey, setApiKey] = useState(() => sessionStorage.getItem('dwts-api-key') || '')
  const [system, setSystem] = useState(null)
  const [modelMetrics, setModelMetrics] = useState(null)
  const [operations, setOperations] = useState(null)
  const [audit, setAudit] = useState([])
  const [loadingOperations, setLoadingOperations] = useState(false)
  const [screening, setScreening] = useState(false)
  const [screen, setScreen] = useState({ sender: '', recipient: '', value_eth: '0', gas: '21000', gas_price_wei: '', nonce: '', input: '0x' })
  const [screenResult, setScreenResult] = useState(null)
  const [reviewer, setReviewer] = useState('reviewer')
  const [notice, setNotice] = useState('')
  const [walletQuery, setWalletQuery] = useState('')
  const [walletResults, setWalletResults] = useState([])
  const [walletDetail, setWalletDetail] = useState(null)
  const screenRef = useRef(screen)
  screenRef.current = screen
  const walletRequest = useRef(0)
  const searchRequest = useRef(0)
  const [reviewing, setReviewing] = useState(false)
  const blockRef = useRef(null)
  const modeRef = useRef('simulated')
  const latest = feed[0]
  const decisionEvidence = screenResult || latest
  const live = mode === 'live'
  const pending = mode === 'pending'
  const chart = [...feed].reverse().map((item, index) => ({ event: index + 1, score: item.trust_score, risk: Math.round(item.fraud_probability * 100) }))
  const providerReady = Boolean(system?.connected)
  const pendingReady = Boolean(system?.pending?.configured)
  const needsApiKey = Boolean(system?.authentication_required && !apiKey)
  const selectedModel = modelMetrics?.models?.XGBoost

  async function request(path, options = {}) {
    const headers = { ...(options.body ? {'Content-Type': 'application/json'} : {}), ...(apiKey ? {'X-API-Key': apiKey} : {}), ...options.headers }
    const response = await fetch(`${API}${path}`, {...options, headers})
    return readResponse(response)
  }

  async function tick(activeMode, isCurrent) {
    try {
      const path = activeMode === 'live'
        ? `/api/blockchain/live-stream${blockRef.current != null ? `?after_block=${blockRef.current}` : ''}`
        : activeMode === 'pending' ? '/api/blockchain/pending-stream' : '/api/stream/next'
      const payload = await request(path)
      if (!isCurrent() || modeRef.current !== activeMode) return false
      if (activeMode === 'live') {
        setBlock(payload.block_number)
        blockRef.current = payload.block_number
        setFeed(items => payload.new_block || items.length === 0 ? [...payload.transactions].reverse().concat(items).slice(0, 24) : items)
      } else if (activeMode === 'pending') {
        if (payload.error) throw new Error(payload.error)
        setFeed(payload.transactions)
      } else {
        setFeed(items => [payload, ...items].slice(0, 24))
      }
      setError('')
      return true
    } catch (exception) {
      if (!isCurrent()) return false
      setRunning(false)
      setError(exception.message)
      return false
    }
  }

  useEffect(() => {
    let active = true
    let timer
    async function poll() {
      const success = await tick(mode, () => active)
      if (active && success) timer = setTimeout(poll, MODES[mode].interval)
    }
    if (running) poll()
    return () => { active = false; clearTimeout(timer) }
  }, [running, mode, apiKey])

  useEffect(() => setScreenResult(null), [screen])

  useEffect(() => {
    Promise.all([request('/api/blockchain/status'), request('/api/model/metrics')])
      .then(([status, metrics]) => { setSystem(status); setModelMetrics(metrics) })
      .catch(exception => setError(exception.message))
  }, [])

  useEffect(() => {
    document.documentElement.dataset.theme = theme
    document.querySelector('meta[name="theme-color"]')?.setAttribute('content', theme === 'dark' ? '#111216' : '#f2f3f7')
  }, [theme])

  function toggleTheme() {
    setTheme(current => {
      const next = current === 'dark' ? 'light' : 'dark'
      try { localStorage.setItem('dwts-theme', next) } catch { /* Theme still works for this visit. */ }
      return next
    })
  }

  useEffect(() => {
    const ids = ['overview', 'screening', 'investigation', 'intelligence', 'operations']
    function updateActiveSection() {
      const offset = 170
      const visible = ids.filter(id => document.getElementById(id)?.getBoundingClientRect().top <= offset)
      setActiveSection(visible.at(-1) || 'overview')
    }
    updateActiveSection()
    window.addEventListener('scroll', updateActiveSection, { passive: true })
    window.addEventListener('resize', updateActiveSection)
    return () => { window.removeEventListener('scroll', updateActiveSection); window.removeEventListener('resize', updateActiveSection) }
  }, [])

  function selectMode(nextMode) {
    if ((nextMode === 'live' && !providerReady) || (nextMode === 'pending' && !pendingReady)) return
    modeRef.current = nextMode
    setRunning(false); setFeed([]); setBlock(null); setScreenResult(null); setError('')
    blockRef.current = null
    setMode(nextMode)
  }

  function rememberKey(value) {
    setApiKey(value)
    if (value) sessionStorage.setItem('dwts-api-key', value)
    else sessionStorage.removeItem('dwts-api-key')
  }

  async function loadOperations() {
    setLoadingOperations(true)
    try {
      const [status, metrics, audits] = await Promise.all([
        request('/api/blockchain/status'), request('/api/operations/metrics'), request('/api/operations/audit?limit=6'),
      ])
      setSystem(status); setOperations(metrics); setAudit(audits); setNotice('Operational evidence refreshed.')
    } catch (exception) { setNotice(exception.message) }
    finally { setLoadingOperations(false) }
  }

  async function submitScreen(event) {
    event.preventDefault(); setScreening(true)
    const submitted = screen
    const submittedMode = modeRef.current
    setScreenResult(null)
    try {
      const payload = {
        sender: screen.sender, recipient: screen.recipient || null,
        value_eth: Number(screen.value_eth), gas: Number(screen.gas), input: screen.input || '0x',
        gas_price_wei: screen.gas_price_wei === '' ? null : Number(screen.gas_price_wei),
        nonce: screen.nonce === '' ? null : Number(screen.nonce),
      }
      const result = await request('/api/screen-transaction', {method: 'POST', body: JSON.stringify(payload)})
      if (screenRef.current === submitted && modeRef.current === submittedMode) {
        setScreenResult({ ...result, address: submitted.sender, source: 'Proposed transaction' })
        setNotice('Screening complete. DWTS was not changed.')
      }
    } catch (exception) { if (screenRef.current === submitted && modeRef.current === submittedMode) setNotice(exception.message) }
    finally { setScreening(false) }
  }

  async function submitReview(label) {
    if (!latest?.address || reviewing) return
    setReviewing(true)
    try {
      await request('/api/labels', {method: 'POST', body: JSON.stringify({
        transaction_hash: latest.transaction_hash || null, address: latest.address, label, reviewer,
        notes: `Dashboard review of ${latest.source || mode} result`,
      })})
      setNotice(`Saved as ${label ? 'confirmed fraud' : 'legitimate'} by ${reviewer}.`)
    } catch (exception) { setNotice(exception.message) }
    finally { setReviewing(false) }
  }

  async function searchWallets(event) {
    event.preventDefault()
    const id = ++searchRequest.current
    ++walletRequest.current
    try {
      const results = await request(`/api/wallets?query=${encodeURIComponent(walletQuery.trim())}&limit=25`)
      if (id !== searchRequest.current) return
      setWalletResults(results); setWalletDetail(null); setNotice(`${results.length} wallet records found.`)
    } catch (exception) { if (id === searchRequest.current) setNotice(exception.message) }
  }

  async function inspectWallet(address) {
    const id = ++walletRequest.current
    try { const result = await request(`/api/wallets/${address}`); if (id === walletRequest.current) { setWalletDetail(result); setScorePage(0) } }
    catch (exception) { if (id === walletRequest.current) setNotice(exception.message) }
  }

  function exportWallet() {
    if (!walletDetail) return
    const headers = [
      'record_id', 'wallet_address', 'current_wallet_score', 'wallet_updated_at',
      'history_timestamp', 'history_score', 'score_change', 'fraud_probability',
      'fraud_percent', 'risk_action', 'dwts_applied', 'source_type', 'source_key',
      'block_number', 'dwts_explanation', 'latest_model_explanation',
    ]
    const history = walletDetail.history.map((item, index) => {
      const previous = index ? walletDetail.history[index - 1].score : 70
      const change = Number((item.score - previous).toFixed(2))
      const source = item.source_key?.split(':')[0] || 'api_or_replay'
      const dwtsExplanation = item.applied === false
        ? `Observation recorded but not applied because the wallet was still in the cold-start period; score remained ${item.score}.`
        : `DWTS changed ${change >= 0 ? 'up' : 'down'} by ${Math.abs(change)} points from ${previous} to ${item.score} after a ${(item.fraud_probability * 100).toFixed(2)}% fraud estimate, resulting in ${item.action}.`
      const latestExplanation = index === walletDetail.history.length - 1 && latest?.address === walletDetail.address
        ? (latest.explanation || []).map(reason => `${reason.feature}: ${reason.direction} (${reason.impact})`).join('; ')
        : 'Feature explanation was not persisted for this historical record.'
      return [item.id, walletDetail.address, walletDetail.score, walletDetail.updated_at, item.created_at, item.score, change, item.fraud_probability, (item.fraud_probability * 100).toFixed(2), item.action, item.applied, source, item.source_key, item.block_number, dwtsExplanation, latestExplanation]
    })
    const rows = [headers, ...history]
    const csv = rows.map(row => row.map(value => `"${String(value ?? '').replaceAll('"', '""')}"`).join(',')).join('\n')
    const link = document.createElement('a')
    link.href = URL.createObjectURL(new Blob([csv], {type: 'text/csv'}))
    link.download = `${walletDetail.address}-dwts-evidence.csv`; link.click(); URL.revokeObjectURL(link.href)
  }

  function loadEvent(item = latest) {
    if (!item?.address) return
    setScreen({
      sender: item.address,
      recipient: item.recipient || '',
      value_eth: String(item.value_eth ?? 0),
      gas: String(item.gas ?? 21000),
      gas_price_wei: item.gas_price_wei == null ? '' : String(item.gas_price_wei),
      nonce: item.nonce == null ? '' : String(item.nonce),
      input: item.input?.startsWith('0x') ? item.input : '0x',
    })
    setNotice(item.value_eth == null ? 'Loaded the latest wallet with safe transaction defaults.' : 'Loaded all available fields from the latest transaction.')
    document.getElementById('screening')?.scrollIntoView({behavior: 'smooth'})
  }

  return <div className="app-shell">
    <header className="topbar">
      <div className="brand-lockup"><LogoMark/><div><p>DWTS</p><span>AI Fraud Detection Layer</span></div></div>
      <div className="top-actions">
        <div className="system-strip" aria-label="System status">
          <StatusDot active={system != null} label="API" />
          <StatusDot active={providerReady} label="Ethereum" />
          <span className="model-stamp">MODEL {modelMetrics?.model_version?.slice(0, 8) || '—'}</span>
        </div>
        <button className="theme-toggle" type="button" onClick={toggleTheme} aria-label={`Switch to ${theme === 'dark' ? 'light' : 'dark'} mode`} aria-pressed={theme === 'dark'} title={`Use ${theme === 'dark' ? 'light' : 'dark'} mode`}><span aria-hidden="true">{theme === 'dark' ? '☀' : '☾'}</span>{theme === 'dark' ? 'Light' : 'Dark'}</button>
      </div>
    </header>

    <a className="skip-link" href="#workspace">Skip to workspace</a>
    <nav className="section-nav" aria-label="Workspace sections">{[['overview', 'Overview'], ['screening', 'Screen transaction'], ['investigation', 'Wallet history'], ['intelligence', 'Intelligence'], ['operations', 'Operations']].map(([id, label]) => <a key={id} href={`#${id}`} aria-current={activeSection === id ? 'location' : undefined} onClick={() => setActiveSection(id)}>{label}</a>)}</nav>
    <main className="workspace" id="workspace">
      <section className="command-header" id="overview">
        <div><h1>Understand a wallet’s risk</h1><p className="lede">Check a transfer, explore wallet activity, and understand what raised a concern.</p></div>
        <div className="mode-switcher" role="group" aria-label="Data source">
          {Object.entries(MODES).map(([key, value]) => {
            const disabled = (key === 'live' && !providerReady) || (key === 'pending' && !pendingReady)
            return <button key={key} type="button" disabled={disabled} aria-pressed={mode === key} title={disabled ? 'Configure the matching provider to enable this source' : value.note} onClick={() => selectMode(key)}><span>{value.label}</span><small>{disabled ? 'Not configured' : value.note}</small></button>
          })}
        </div>
      </section>

      <aside className="workspace-guide"><strong>New here? Start with Dataset replay.</strong><p>Press Start to see example wallet assessments, then use an event to fill the transaction form. Trust is scored out of 100: higher is better. Estimated fraud risk is a model prediction, not proof of fraud.</p></aside>

      {(live || pending || error) && <div className={`source-notice ${error ? 'notice-error' : ''}`} role={error ? 'alert' : 'status'}><span className="pulse-dot" />{error || (live ? `Confirmed-chain mode. ${block ? `Block ${block}. ` : ''}Cold wallets remain in Observe.` : 'Pending decisions are advisory and never change DWTS before confirmation.')}</div>}

      <section className="decision-rail" aria-label="Current risk decision">
        <div className="rail-identity"><p className="eyebrow">Current wallet</p><strong>{latest ? shortAddress(latest.address) : 'No wallet selected'}</strong><span>{latest?.source || MODES[mode].note}</span></div>
        <div className="rail-meter"><MetricBar label="Trust" value={latest?.trust_score} suffix="/100" tone="trust" /><MetricBar label="Fraud likelihood" value={latest ? latest.fraud_probability * 100 : null} suffix="%" tone="risk" /></div>
        <div className="rail-decision"><p>Suggested next step</p><RiskBadge action={latest?.risk_action || 'Waiting'} large /><span>{latest ? `${latest.latency_ms ?? latest.decision_ms ?? '—'} ms decision` : 'Press Start to see a result'}</span></div>
        <button className={`stream-control ${running ? 'is-running' : ''}`} type="button" onClick={() => setRunning(value => !value)}><span className="control-glyph" aria-hidden="true">{running ? 'Ⅱ' : '▶'}</span><span>{running ? 'Pause' : 'Start'}<small>{MODES[mode].label}</small></span></button>
      </section>

      <section className="monitor-grid">
        <div className="panel trace-panel">
          <PanelHeading kicker="DECISION TRACE" title="Latest observed wallets" meta={`${feed.length} events in view`} />
          <div className="trace-chart">
            {chart.length ? <ResponsiveContainer width="100%" height="100%"><AreaChart data={chart} margin={{top: 10, right: 4, left: -20, bottom: 0}}><defs><linearGradient id="trust-fill" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stopColor="var(--trust-accent)" stopOpacity=".28"/><stop offset="1" stopColor="var(--trust-accent)" stopOpacity="0"/></linearGradient></defs><CartesianGrid stroke="var(--grid-line)" vertical={false}/><XAxis dataKey="event" stroke="var(--text-muted)" tickLine={false} axisLine={false}/><YAxis domain={[0, 100]} stroke="var(--text-muted)" tickLine={false} axisLine={false}/><Tooltip content={<TraceTooltip />} /><Area type="monotone" dataKey="score" stroke="var(--trust-accent)" strokeWidth={2} fill="url(#trust-fill)" isAnimationActive={false}/></AreaChart></ResponsiveContainer> : <EmptyState title="No decisions yet" body={`Start ${MODES[mode].label.toLowerCase()} to populate the trace.`} />}
          </div>
          <p className="chart-disclaimer">Each point is a different incoming wallet observation—not one wallet’s score history.</p>
        </div>

        <div className="panel feed-panel">
          <PanelHeading kicker="EVENT LEDGER" title={live ? 'Confirmed transactions' : pending ? 'Pending transactions' : 'Historical snapshots'} meta={running ? 'Receiving' : 'Paused'} />
          <div className="event-ledger">{feed.length ? feed.map(item => <article className={`event-row ${screen.sender === item.address ? 'is-selected' : ''}`} key={item.sequence}>
            <button className="event-address" type="button" aria-pressed={screen.sender === item.address} onClick={() => loadEvent(item)} title="Load this event into pre-broadcast screening"><span className="address-ident" aria-hidden="true">{item.address.slice(2, 4).toUpperCase()}</span><span><strong>{shortAddress(item.address)}</strong><small>{live ? `Block ${item.block_number} · ${item.value_eth} ETH` : pending ? `${item.status} · ${item.value_eth} ETH` : `Snapshot ${item.sequence} · label ${item.actual_label}`}</small></span></button>
            <div className="event-risk"><strong>{(item.fraud_probability * 100).toFixed(1)}%</strong><RiskBadge action={item.risk_action} /></div>
            {item.contract_call && <p className="contract-note">{item.contract_call.method} · {item.contract_call.selector}</p>}
          </article>) : <EmptyState title="Ledger is empty" body="Incoming decisions will appear here." />}</div>
        </div>
      </section>

      <section className="work-grid">
        <div className="panel screening-panel" id="screening">
          <PanelHeading kicker="PRE-BROADCAST GATE" title="Screen a proposed transaction" meta="Advisory · no state change" />
          <div className="latest-shortcut"><div><strong>Quick-fill from the event ledger</strong><span>{latest ? `${shortAddress(latest.address)} · ${latest.value_eth == null ? 'wallet snapshot' : `${latest.value_eth} ETH`}` : 'Start a stream to capture an event'}</span></div><button type="button" disabled={!latest} onClick={() => loadEvent()}>Use latest event <span aria-hidden="true">↓</span></button></div>
          {needsApiKey && <p className="source-notice access-notice">Screening is protected. Paste your <code>API_KEY</code> from <code>.env</code> under <a href="#operations">Operations and access</a>, then try again.</p>}
          <form onSubmit={submitScreen} className="form-stack">
            <Field label="Sender wallet" hint="Required"><input required pattern="0x[a-fA-F0-9]{40}" placeholder="0x…" value={screen.sender} onChange={event => setScreen({...screen, sender: event.target.value})}/></Field>
            <Field label="Recipient wallet" hint="Optional for contract creation"><input pattern="0x[a-fA-F0-9]{40}" placeholder="0x…" value={screen.recipient} onChange={event => setScreen({...screen, recipient: event.target.value})}/></Field>
            <div className="field-pair"><Field label="Value" hint="ETH"><input required type="number" min="0" step="any" value={screen.value_eth} onChange={event => setScreen({...screen, value_eth: event.target.value})}/></Field><Field label="Gas limit"><input required type="number" min="21000" step="1" value={screen.gas} onChange={event => setScreen({...screen, gas: event.target.value})}/></Field></div>
            <details className="advanced-fields"><summary>Advanced transaction fields</summary><div className="field-pair"><Field label="Gas price" hint="wei"><input type="number" min="0" placeholder="Optional" value={screen.gas_price_wei} onChange={event => setScreen({...screen, gas_price_wei: event.target.value})}/></Field><Field label="Nonce"><input type="number" min="0" placeholder="Optional" value={screen.nonce} onChange={event => setScreen({...screen, nonce: event.target.value})}/></Field></div><Field label="Contract calldata" hint="Hex encoded"><input pattern="0x[a-fA-F0-9]*" value={screen.input} onChange={event => setScreen({...screen, input: event.target.value})}/></Field></details>
            <button className="primary-action" disabled={screening} type="submit">{screening ? 'Evaluating…' : 'Run screening'}<span aria-hidden="true">→</span></button>
          </form>
          {screenResult && <div className="screen-result"><div><p>Screening result</p><RiskBadge action={screenResult.risk_action} large /></div><div className="result-numbers"><span><small>Trust</small>{screenResult.projected_trust_score}</span><span><small>Fraud</small>{(screenResult.fraud_probability * 100).toFixed(1)}%</span><span><small>Evidence</small>{screenResult.observed_events} events</span></div>{screenResult.contract_call && <p className="decoded-call">Decoded call: {screenResult.contract_call.method} · {screenResult.contract_call.selector}</p>}<p className="scope-warning">Uses a wallet-behavior preview. It is not a transaction-trained classifier and does not update DWTS.</p></div>}
        </div>

        <div className="panel evidence-panel">
        <PanelHeading kicker="MODEL EVIDENCE" title="Why this decision" meta={decisionEvidence?.model_version ? `v${decisionEvidence.model_version.slice(0, 8)}` : 'Awaiting event'} />
          {decisionEvidence ? <><div className="evidence-summary"><span><small>Source</small>{decisionEvidence.source || decisionEvidence.confidence || 'Historical validation'}</span><span><small>Model latency</small>{decisionEvidence.latency_ms ?? decisionEvidence.decision_ms ?? '—'} ms</span><span><small>Anomaly candidate</small>{decisionEvidence.anomaly_score == null ? 'Quality-gated off' : `${(decisionEvidence.anomaly_score * 100).toFixed(1)}%`}</span></div><div className="reason-list">{decisionEvidence.explanation?.length ? decisionEvidence.explanation.map(item => <div key={item.feature}><span className={item.direction === 'higher risk' ? 'impact-up' : 'impact-down'}>{item.impact > 0 ? '+' : ''}{item.impact}</span><p><strong>{item.feature}</strong><small>{item.direction}</small></p></div>) : <EmptyState title="No explanation available" body="This source did not return feature contributions." />}</div>{!screenResult && <div className="review-row"><Field label="Reviewer"><input value={reviewer} minLength="2" onChange={event => setReviewer(event.target.value)}/></Field><div><button type="button" disabled={reviewing || reviewer.trim().length < 2} className="safe-action" onClick={() => submitReview(0)}>Mark legitimate</button><button type="button" disabled={reviewing || reviewer.trim().length < 2} className="risk-action" onClick={() => submitReview(1)}>Mark fraud</button></div></div>}</> : <EmptyState title="Evidence appears after a decision" body="Start a stream or screen a transaction to inspect the model output." />}
        </div>
      </section>

      <section className="panel investigation-panel" id="investigation">
        <PanelHeading kicker="CASEWORK" title="Wallet investigation" meta="Search · history · export" />
        <form onSubmit={searchWallets} className="search-bar"><input aria-label="Wallet address search" placeholder="Search a full or partial wallet address" value={walletQuery} onChange={event => setWalletQuery(event.target.value)}/><button type="submit">Search ledger</button></form>
        <div className="investigation-grid"><div className="wallet-results">{walletResults.length ? walletResults.map(item => <button type="button" key={item.address} className={walletDetail?.address === item.address ? 'selected' : ''} onClick={() => inspectWallet(item.address)}><span className="address-ident">{item.address.slice(2, 4).toUpperCase()}</span><span><strong>{shortAddress(item.address)}</strong><small>Updated {formatDate(item.updated_at)}</small></span><span className="result-score">{item.score}<RiskBadge action={item.risk_action}/></span></button>) : <EmptyState title="No investigation loaded" body="Search blank to see recent wallets, or enter part of an address." />}</div><div className="wallet-case">{walletDetail ? <><div className="case-heading"><div><p>Wallet address</p><strong>{walletDetail.address}</strong></div><button type="button" onClick={exportWallet}>Export evidence CSV</button></div><div className="case-score"><span>{walletDetail.score}</span><div><strong>Current trust score</strong><small>{walletDetail.history.length} recorded decisions</small></div></div><div className="history-list">{[...walletDetail.history].reverse().slice(scorePage * 12, (scorePage + 1) * 12).map((item, index) => <div key={`${item.created_at}-${index}`}><span className="history-line"/><p><strong>{item.score}</strong><small>{formatDate(item.created_at)}</small></p><RiskBadge action={item.action}/><span>{(item.fraud_probability * 100).toFixed(1)}% fraud</span></div>)}</div><div className="pagination"><button disabled={scorePage === 0} onClick={() => setScorePage(p => p - 1)}>Newer scores</button><span>Page {scorePage + 1} of {Math.max(1, Math.ceil(walletDetail.history.length / 12))}</span><button disabled={(scorePage + 1) * 12 >= walletDetail.history.length} onClick={() => setScorePage(p => p + 1)}>Older scores</button></div></> : <EmptyState title="Select a wallet case" body="Its score history and export controls will appear here." />}</div></div>
      </section>

      <Suspense fallback={<p role="status">Loading analysis tools…</p>}><Intelligence api={API} apiKey={apiKey} authRequired={Boolean(system?.authentication_required)} mode={mode} screen={screen} latest={latest}/></Suspense>

      <section className="panel operations-panel" id="operations">
        <div className="operations-head"><PanelHeading kicker="SYSTEM ASSURANCE" title="Operations and access" meta={apiKey ? 'Credential supplied' : 'Public view'} /><button type="button" onClick={loadOperations} disabled={loadingOperations}>{loadingOperations ? 'Refreshing…' : 'Refresh protected evidence'}</button></div>
        <div className="access-row"><Field label="Role API key" hint="Stored only in this browser tab"><input type="password" placeholder="Viewer, reviewer, or admin key" value={apiKey} onChange={event => rememberKey(event.target.value)}/></Field><p>{system?.authentication_required ? <>For this local setup, paste <code>API_KEY</code> from <code>.env</code>. Viewer keys can screen, reviewer keys can save outcomes, and admin keys can open operational evidence.</> : 'This server has no API-key protection configured. Protected actions are available locally.'}</p></div>
        <div className="operations-grid">
          <SystemMetric label="Provider" value={system?.connected ? 'Connected' : 'Offline'} tone={system?.connected ? 'good' : 'muted'} />
          <SystemMetric label="Pending feed" value={system?.pending?.configured ? `${system.pending.provider_count} configured` : 'Disabled'} />
          <SystemMetric label="Model ROC-AUC" value={selectedModel?.roc_auc ?? '—'} tone="good" />
          <SystemMetric label="False-positive rate" value={selectedModel ? `${(selectedModel.false_positive_rate * 100).toFixed(2)}%` : '—'} />
          <SystemMetric label="Block lag" value={operations?.block_lag ?? '—'} />
          <SystemMetric label="Database" value={operations?.database_backend ?? '—'} />
          <SystemMetric label="Wallets scored" value={operations?.wallets ?? '—'} />
          <SystemMetric label="Reviewed labels" value={operations?.reviewed_labels ?? '—'} />
        </div>
        {modelMetrics?.anomaly_detection && <p className="quality-note">Anomaly candidate: {modelMetrics.anomaly_detection.deployment_status} at ROC-AUC {modelMetrics.anomaly_detection.roc_auc}; it is excluded from decisions unless it passes the 0.60 gate.</p>}
        {audit.length > 0 && <div className="audit-log"><p>Recent system requests</p>{audit.map(item => <div key={item.id}><span>{item.method}</span><code>{item.path}</code><strong>{item.status}</strong><small>{Number(item.duration_ms).toFixed(1)} ms</small></div>)}</div>}
      </section>
    </main>

    {notice && <div className="toast" role="status"><span className="pulse-dot" />{notice}<button type="button" aria-label="Dismiss notification" onClick={() => setNotice('')}>×</button></div>}
    <footer><span>DWTS / FRAUD LAYER</span><p>Decision support only. This prototype does not move funds or stop unrelated transactions.</p><a href={`${API}/docs`} target="_blank" rel="noreferrer">API docs ↗</a></footer>
  </div>
}

function PanelHeading({ title, meta }) { return <div className="panel-heading"><h2>{title}</h2><span>{meta}</span></div> }
function Field({ label, hint, children }) { return <label className="field"><span>{label}<small>{hint}</small></span>{children}</label> }
function EmptyState({ title, body }) { return <div className="empty-state"><span aria-hidden="true">◇</span><strong>{title}</strong><p>{body}</p></div> }
function StatusDot({ active, label }) { return <span className="status-dot"><i className={active ? 'active' : ''}/>{label}</span> }
function RiskBadge({ action, large = false }) { return <span className={`risk-badge risk-${String(action).toLowerCase()} ${large ? 'is-large' : ''}`}>{action}</span> }
function SystemMetric({ label, value, tone = '' }) { return <div className={`system-metric ${tone}`}><span>{label}</span><strong>{String(value)}</strong></div> }
function MetricBar({ label, value, suffix, tone }) { const number = value == null ? 0 : Math.max(0, Math.min(100, Number(value))); return <div className={`metric-bar ${tone}`}><div><span>{label}</span><strong>{value == null ? '—' : `${Number(value).toFixed(value % 1 ? 1 : 0)}${suffix}`}</strong></div><div className="bar-track"><i style={{width: `${number}%`}}/></div></div> }
function TraceTooltip({ active, payload }) { if (!active || !payload?.length) return null; const row = payload[0].payload; return <div className="trace-tooltip"><strong>Event {row.event}</strong><span>Trust {row.score}</span><span>Fraud {row.risk}%</span></div> }
function LogoMark() { return <svg className="brand-mark" viewBox="0 0 40 40" role="img" aria-label="DWTS shield and trust network"><path className="logo-shield" d="M20 3 34 9v10c0 8.4-5.2 14.2-14 18-8.8-3.8-14-9.6-14-18V9L20 3Z"/><path className="logo-signal" d="M10 21h5l2.5-6 4.5 11 2.5-5H30"/><circle cx="10" cy="21" r="1.7"/><circle cx="30" cy="21" r="1.7"/></svg> }
function shortAddress(address = '') { return address.length > 16 ? `${address.slice(0, 8)}…${address.slice(-6)}` : address }
function formatDate(value) { return formatApiDate(value) }
