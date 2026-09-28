import { useEffect, useRef, useState } from 'react'
import { Area, AreaChart, CartesianGrid, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts'
import { readResponse } from './api'
import WalletGraph from './WalletGraph'

const short = value => value ? `${value.slice(0, 8)}…${value.slice(-5)}` : '—'
const percent = value => value == null ? 'Unavailable' : `${(value * 100).toFixed(1)}%`
const signedPoints = value => { const points = Number(value || 0) * 100; return `${points > 0 ? '+' : ''}${points.toFixed(1)} points` }
const date = stamp => new Date(stamp * 1000).toLocaleString()

export default function Intelligence({ api, apiKey, authRequired, mode, screen, latest }) {
  const [overview, setOverview] = useState(null)
  const [source, setSource] = useState(mode)
  const [alerts, setAlerts] = useState([])
  const [error, setError] = useState('')
  const [refreshError, setRefreshError] = useState('')
  const [inspecting, setInspecting] = useState(false)
  const [address, setAddress] = useState('')
  const [casework, setCasework] = useState(null)
  const [historyPage, setHistoryPage] = useState(null)
  const [step, setStep] = useState(0)
  const [playing, setPlaying] = useState(false)
  const [metric, setMetric] = useState('observations')
  const [simulation, setSimulation] = useState({ sender: '', originalValue: '0', testValue: '1', originalRecipient: '', testRecipient: '' })
  const [comparison, setComparison] = useState(null)
  const [busy, setBusy] = useState(false)
  const [selected, setSelected] = useState(null)
  const investigationRequest = useRef(0)
  const simulationRequest = useRef(0)
  useEffect(() => setSource(mode), [mode])

  async function request(path, options = {}) {
    const response = await fetch(`${api}/api/intelligence${path}`, { ...options, headers: { 'Content-Type': 'application/json', ...(apiKey ? { 'X-API-Key': apiKey } : {}) } })
    return readResponse(response)
  }

  useEffect(() => {
    let active = true
    let timer
    setOverview(null); setSelected(null)
    async function refresh() {
      try {
        const [next, notices] = await Promise.all([request(`/overview?source=${source}`), request('/alerts')])
        if (active) { setOverview(next); setAlerts(notices); setRefreshError('') }
      } catch (exception) { if (active) setRefreshError(exception.message) }
      finally { if (active) timer = setTimeout(refresh, 10000) }
    }
    refresh()
    return () => { active = false; clearTimeout(timer) }
  }, [source, apiKey])

  useEffect(() => {
    if (!playing) return
    if (step >= (casework?.timeline.length || 0) - 1) { setPlaying(false); return }
    const timer = setTimeout(() => setStep(value => value + 1), 1200)
    return () => clearTimeout(timer)
  }, [playing, casework, step])

  async function inspect(wallet, observation = null, page = 1, until = null) {
    setSelected(observation); setPlaying(false); setInspecting(true); setError(''); setCasework(null)
    const currentRequest = ++investigationRequest.current
    try {
      const path = `/wallet/${encodeURIComponent(wallet)}`
      const [value, history] = await Promise.all([request(path), request(`${path}/history?page=${page}&page_size=50${until == null ? '' : `&until=${until}`}`)])
      if (currentRequest === investigationRequest.current) {
        setHistoryPage(history); setCasework({ ...value, timeline: [...history.items].reverse() }); setAddress(wallet); setStep(0)
      }
    } catch (exception) { if (currentRequest === investigationRequest.current) setError(exception.message) }
    finally { if (currentRequest === investigationRequest.current) setInspecting(false) }
  }
  async function acknowledge(id) {
    try {
      await request(`/alerts/${encodeURIComponent(id)}/acknowledge`, { method: 'POST' })
      setAlerts(items => items.map(item => item.id === id ? { ...item, acknowledged: true } : item))
    } catch (exception) { setError(exception.message) }
  }
  function loadSimulation(source = screen) {
    ++simulationRequest.current
    setBusy(false); setError('')
    const original = String(source.value_eth ?? 0)
    setSimulation({
      sender: source.sender || source.address || '', originalValue: original,
      testValue: String(Number(original || 0) + 1),
      originalRecipient: source.recipient || '', testRecipient: source.recipient || '',
    })
    setComparison(null)
  }
  async function simulate(event) {
    event.preventDefault(); setBusy(true); setError(''); setComparison(null)
    const id = ++simulationRequest.current
    const common = { sender: simulation.sender, gas: 21000, input: '0x' }
    const baseline = { ...common, recipient: simulation.originalRecipient || null, value_eth: Number(simulation.originalValue) }
    const scenario = { ...common, recipient: simulation.testRecipient || null, value_eth: Number(simulation.testValue) }
    try { const result = await request('/simulate', { method: 'POST', body: JSON.stringify({ baseline, scenario }) }); if (id === simulationRequest.current) setComparison(result) }
    catch (exception) { if (id === simulationRequest.current) setError(exception.message) }
    finally { if (id === simulationRequest.current) setBusy(false) }
  }
  function editSimulation(name, value) { ++simulationRequest.current; setBusy(false); setSimulation(current => ({ ...current, [name]: value })); setComparison(null) }
  const simulationReady = /^0x[a-fA-F0-9]{40}$/.test(simulation.sender) && simulation.originalValue !== '' && simulation.testValue !== ''
  const simulationChanged = Number(simulation.originalValue) !== Number(simulation.testValue) || simulation.originalRecipient.toLowerCase() !== simulation.testRecipient.toLowerCase()
  const current = casework?.timeline[step]
  const evidence = selected || (casework || inspecting ? (current?.kind === 'decision' ? current.data : null) : latest)
  const graph = overview?.graph
  const monitoring = overview?.monitoring

  return <section className="intelligence" id="intelligence" aria-label="Fraud intelligence">
    <div className="panel-heading"><div><h2>Fraud intelligence</h2><p>Patterns, connections, and the evidence behind each decision.</p></div><span>Refreshes every 10 seconds</span></div>
    <label className="intel-control">Evidence source<select aria-label="Evidence source" value={source} onChange={event => setSource(event.target.value)}><option value="simulated">Dataset replay</option><option value="live">Retained confirmed chain</option><option value="pending">Pending observations</option><option value="api">API predictions</option></select></label>
    {(error || refreshError) && <p className="source-notice notice-error" role="alert">{error || refreshError}{error && <button onClick={() => setError('')} aria-label="Dismiss intelligence error">Dismiss</button>}</p>}
    <p className="intel-note">{overview ? 'Latest' : 'Loading evidence…'} {overview?.observations ?? '—'} recorded observations and {overview?.chain_events ?? 0} retained chain events, each capped at 2,000. {overview?.truncated ? 'Sample limit reached. ' : ''}Fraud rate means model-flagged observations (≥80%), not confirmed fraud. Chain relationships require a configured provider.</p>
    <div className="intel-grid">
      <article className="panel">
        <Heading title="Activity over time" />
        <label className="intel-control">Measure<select aria-label="Measure" value={metric} onChange={event => setMetric(event.target.value)}>
          <option value="observations">Risk observations</option><option value="transactions">Native transaction count</option><option value="eth_volume">Successful / status-unknown ETH volume</option><option value="fraud_rate">Model-flagged rate (%)</option><option value="dwts">Average DWTS</option>
        </select></label>
        {overview?.trends.length ? <div className="intel-chart"><ResponsiveContainer width="100%" height="100%"><AreaChart data={overview.trends.map(row => ({ ...row, fraud_rate: row.fraud_rate == null ? null : row.fraud_rate * 100 }))} margin={{ top: 12, right: 16, bottom: 8, left: 0 }}><CartesianGrid stroke="var(--line)"/><XAxis dataKey="timestamp" tickFormatter={value => new Date(value * 1000).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })}/><YAxis width={55} domain={metric === 'fraud_rate' || metric === 'dwts' ? [0, 100] : [0, 'auto']} tickFormatter={value => metric === 'fraud_rate' ? `${value}%` : value}/><Tooltip labelFormatter={date} contentStyle={{ background: 'var(--raised)', border: '1px solid var(--line-strong)', color: 'var(--paper)' }} formatter={value => metric === 'fraud_rate' ? `${Number(value).toFixed(1)}%` : Number(value).toLocaleString(undefined, { maximumFractionDigits: 3 })}/><Area dataKey={metric} stroke="var(--trust-accent)" fill="var(--trust-accent)" fillOpacity={.12} dot={{ r: 3 }} isAnimationActive={false}/></AreaChart></ResponsiveContainer></div> : <Empty text="Start a source to record trend evidence." />}
        <p className="intel-note">Hourly buckets. ETH and transaction totals use retained native chain events only; token units are excluded.</p>
      </article>
      <article className="panel">
        <Heading title="Wallet transaction graph" />
        <WalletGraph key={source} graph={graph} onInspect={inspect}/>
      </article>
      <article className="panel"><Heading title="Wallets needing attention" /><div className="intel-scroll">{overview?.anomalies.length ? overview.anomalies.map(item => <button className={`intel-event ${selected?.id === item.id ? 'is-selected' : ''}`} aria-pressed={selected?.id === item.id} key={item.id} onClick={() => inspect(item.address, item)}><strong>{short(item.address)}</strong><span>{percent(item.fraud_probability)} risk · {item.source}</span><small>{date(item.timestamp)}</small></button>) : <Empty text="No observations cross the 80% risk or anomaly threshold." />}</div></article>
      <article className="panel"><Heading title="What influenced the risk?" /><p className="intel-note">Selected observation: {short(evidence?.address)}. XGBoost contributions are in raw model log-odds, not percentage points or causal explanations.</p><div className="reason-list">{evidence?.explanation?.length ? evidence.explanation.map(reason => <div key={reason.feature}><span className={reason.impact > 0 ? 'impact-up' : 'impact-down'}>{reason.impact > 0 ? '+' : ''}{reason.impact}</span><p><strong>{reason.feature}</strong><small>{reason.direction}</small></p></div>) : <Empty text="Select an anomaly or replay a recorded decision to see its saved explanation." />}</div></article>
      <article className="panel"><Heading title="Connected high-risk wallets" /><p className="intel-note">Connected components of wallets with ≥80% latest observed risk. These relationships are review leads, not proof of collusion.</p><div className="intel-scroll">{overview?.clusters.length ? overview.clusters.map(cluster => <div className="intel-cluster" key={cluster.members[0]}><strong>{cluster.size} connected suspicious wallets</strong>{cluster.members.map(wallet => <button key={wallet} onClick={() => inspect(wallet)}>{short(wallet)}</button>)}</div>) : <Empty text="No connected suspicious wallet groups in the observed graph." />}</div></article>
      <article className="panel"><Heading title="Model health" /><div className="operations-grid"><Stat label="Mean decision latency" value={monitoring?.mean_ms == null ? '—' : `${monitoring.mean_ms.toFixed(2)} ms`}/><Stat label="P95 latency" value={monitoring?.p95_ms == null ? '—' : `${monitoring.p95_ms.toFixed(2)} ms`}/><Stat label="Latency samples" value={monitoring?.latency_samples ?? 0}/><Stat label="Drift warnings" value={monitoring?.drift_warnings ?? 0}/></div><p className="intel-note">Prediction distribution · live latency is batch processing time per sender.</p><div className="intel-distribution">{monitoring?.distribution.map(bin => <label key={bin.range}><span>{bin.range}</span><meter min="0" max={Math.max(1, overview.observations)} value={bin.count}/><strong>{bin.count}</strong></label>)}</div></article>
    </div>
    <article className="panel intel-case"><Heading title="Wallet activity and history" /><form className="search-bar" onSubmit={event => { event.preventDefault(); setSelected(null); inspect(address) }}><input required aria-label="Full wallet address for investigation" pattern="0x[a-fA-F0-9]{40}" placeholder="0x… full wallet address" value={address} onChange={event => setAddress(event.target.value)}/><button disabled={inspecting}>{inspecting ? 'Loading history…' : 'Open investigation'}</button></form>
      {casework && <><p className="intel-note">{casework.address} · {casework.scope} {casework.truncated && 'Sample limit reached.'}</p><div className="operations-grid"><Stat label="Observed events" value={casework.profile.events}/><Stat label="Counterparties" value={casework.profile.counterparties.length}/><Stat label="Events per hour" value={casework.profile.transactions_per_hour?.toFixed(2) ?? '—'}/><Stat label="Average gas used" value={casework.profile.average_gas_used?.toFixed(0) ?? 'Unavailable'}/><Stat label="Failure rate" value={percent(casework.profile.failure_rate)}/><Stat label="Receipt samples" value={casework.profile.receipt_samples}/></div>
      {historyPage && <div className="pagination"><button disabled={inspecting || historyPage.page <= 1} onClick={() => inspect(casework.address, null, historyPage.page - 1, historyPage.until)}>Newer history</button><span>{historyPage.total} records · page {historyPage.page} of {Math.max(1, Math.ceil(historyPage.total / historyPage.page_size))}</span><button disabled={inspecting || !historyPage.has_more} onClick={() => inspect(casework.address, null, historyPage.page + 1, historyPage.until)}>Older history</button></div>}
      {casework.timeline.length ? <div className="intel-replay"><div className="intel-buttons"><button disabled={step === 0} onClick={() => { setPlaying(false); setSelected(null); setStep(value => value - 1) }}>Previous</button><button disabled={step >= casework.timeline.length - 1 && !playing} onClick={() => { setSelected(null); setPlaying(value => !value) }}>{playing ? 'Pause replay' : 'Play replay'}</button><button disabled={step >= casework.timeline.length - 1} onClick={() => { setPlaying(false); setSelected(null); setStep(value => value + 1) }}>Next</button><span>{step + 1} / {casework.timeline.length}</span></div><input aria-label="Replay position" type="range" min="0" max={casework.timeline.length - 1} value={step} onChange={event => { setPlaying(false); setSelected(null); setStep(Number(event.target.value)) }}/><strong>{current.kind} · {date(current.timestamp)}</strong><ReplayEvent item={current}/></div> : <Empty text="No retained events or recorded decisions for this wallet." />}</>}
    </article>
    <div className="intel-grid">
      <article className="panel"><Heading title="Alert center" /><p className="intel-note">Latest 100 high-risk observations across sources. Reviewer access can acknowledge. Configure webhook or SMTP delivery on the server.</p><div className="intel-scroll">{alerts.length ? alerts.map(alert => <div className="intel-alert" key={alert.id}><button onClick={() => { setSelected(alert.event); inspect(alert.event.address) }}>{short(alert.event.address)} · {percent(alert.event.fraud_probability)}</button><small>{alert.event.source} · {date(alert.timestamp)} · {Object.entries(alert.delivery).map(([key, value]) => `${key}: ${value}`).join(', ') || 'Dashboard only / delivery pending'}</small><button disabled={alert.acknowledged} onClick={() => acknowledge(alert.id)}>{alert.acknowledged ? 'Acknowledged' : 'Acknowledge'}</button></div>) : <Empty text="No high-risk alerts recorded." />}</div></article>
      <article className="panel simulator-panel"><Heading title="What-if risk simulator" /><p className="intel-note">Preview how a different amount or recipient could affect this wallet. It never sends a transaction or changes the trust score.</p><form className="simulator-form" onSubmit={simulate}>
        {authRequired && !apiKey && <p className="source-notice access-notice">Risk comparison is protected. Paste your <code>API_KEY</code> from <code>.env</code> under <a href="#operations">Operations and access</a>, then compare again.</p>}
        <div className="simulator-step"><span aria-hidden="true">1</span><div><strong>Choose a wallet</strong><p>Enter the sender you want to test.</p></div></div>
        <label className="field">Sender wallet<input required aria-label="Simulator sender wallet" pattern="0x[a-fA-F0-9]{40}" placeholder="0x… full wallet address" value={simulation.sender} onChange={event => editSimulation('sender', event.target.value)}/></label>
        {(screen.sender || latest?.address) && <button className="secondary-action" type="button" onClick={() => loadSimulation(/^0x[a-fA-F0-9]{40}$/.test(screen.sender) ? screen : latest)}>Use {/^0x[a-fA-F0-9]{40}$/.test(screen.sender) ? 'screening form' : 'latest wallet'}</button>}
        <div className="simulator-step"><span aria-hidden="true">2</span><div><strong>Describe the change</strong><p>Compare the original transfer with your test transfer.</p></div></div>
        <div className="scenario-grid"><Field label="Original amount" hint="ETH"><input required aria-label="Original amount ETH" type="number" min="0" step="any" value={simulation.originalValue} onChange={event => editSimulation('originalValue', event.target.value)}/></Field><Field label="Test amount" hint="ETH"><input required aria-label="Test amount ETH" type="number" min="0" step="any" value={simulation.testValue} onChange={event => editSimulation('testValue', event.target.value)}/></Field></div>
        <details className="simulator-optional"><summary>Compare recipient addresses too</summary><div className="scenario-grid"><Field label="Original recipient" hint="Optional"><input aria-label="Original recipient" pattern="0x[a-fA-F0-9]{40}" placeholder="Contract creation if blank" value={simulation.originalRecipient} onChange={event => editSimulation('originalRecipient', event.target.value)}/></Field><Field label="Test recipient" hint="Optional"><input aria-label="Test recipient" pattern="0x[a-fA-F0-9]{40}" placeholder="Contract creation if blank" value={simulation.testRecipient} onChange={event => editSimulation('testRecipient', event.target.value)}/></Field></div></details>
        <div className="simulator-step"><span aria-hidden="true">3</span><div><strong>See the difference</strong><p>The same wallet history is used for both predictions.</p></div></div>
        <button className="primary-action simulator-action" disabled={busy || !simulationReady || !simulationChanged}>{busy ? 'Comparing…' : !simulationChanged ? 'Change an amount or recipient' : 'Compare risk'}</button>
      </form>
      {comparison && <div className="simulation-result" role="status" aria-live="polite"><div><span>Scenario risk estimate</span><strong>{percent(comparison.baseline.fraud_probability)} <b aria-hidden="true">→</b> {percent(comparison.scenario.fraud_probability)}</strong><small className={comparison.probability_delta > 0 ? 'delta-up' : comparison.probability_delta < 0 ? 'delta-down' : ''}>{comparison.probability_delta === 0 ? 'No meaningful change' : `${comparison.probability_delta > 0 ? '+' : ''}${(comparison.probability_delta * 100).toFixed(2)} percentage points`}</small></div><div><span>Projected trust</span><strong>{comparison.baseline.projected_trust_score} <b aria-hidden="true">→</b> {comparison.scenario.projected_trust_score}</strong><small>{comparison.trust_delta === 0 ? 'Unchanged for this preview' : `${comparison.trust_delta > 0 ? '+' : ''}${comparison.trust_delta} points`}</small></div><p>Raw wallet model: {percent(comparison.baseline.model_probability ?? comparison.baseline.fraud_probability)} → {percent(comparison.scenario.model_probability ?? comparison.scenario.fraud_probability)}. {comparison.adjustments ? `What-if adjustment: amount ${signedPoints(comparison.adjustments.amount)}, recipient ${signedPoints(comparison.adjustments.recipient)}.` : 'The scenario includes a bounded transaction adjustment.'} No score or transaction was saved.</p></div>}</article>
    </div>
  </section>
}

function Heading({ title }) { return <div className="panel-heading"><h3>{title}</h3></div> }
function Field({ label, hint, children }) { return <label className="field"><span>{label}<small>{hint}</small></span>{children}</label> }
function Empty({ text }) { return <div className="empty-state"><p>{text}</p></div> }
function Stat({ label, value }) { return <div className="system-metric"><span>{label}</span><strong>{value}</strong></div> }

function ReplayEvent({ item }) {
  const data = item.data
  return <div className="replay-evidence">
    {item.kind === 'decision' ? <dl><div><dt>Wallet</dt><dd>{data.address}</dd></div><div><dt>Estimated risk</dt><dd>{percent(data.fraud_probability)}</dd></div><div><dt>Trust score</dt><dd>{data.trust_score ?? data.projected_trust_score ?? '—'}</dd></div><div><dt>Recommendation</dt><dd>{data.risk_action || 'Observe'}</dd></div></dl>
      : <dl><div><dt>From</dt><dd>{data.sender}</dd></div><div><dt>To</dt><dd>{data.recipient || 'Contract creation'}</dd></div><div><dt>Value</dt><dd>{data.value} {data.kind === 'native' ? 'ETH' : 'raw token units'}</dd></div><div><dt>Execution</dt><dd>{data.status === 1 ? 'Succeeded' : data.status === 0 ? 'Failed' : 'Receipt unavailable'}</dd></div></dl>}
    <details><summary>View raw evidence</summary><pre>{JSON.stringify(data, null, 2)}</pre></details>
  </div>
}
