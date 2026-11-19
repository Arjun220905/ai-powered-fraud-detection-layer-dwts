import { useState } from 'react'
import { findTransferPath, walletAddress, shortWallet } from './investigation'

export default function TransferTrace({ graph, source, onUseChain, onInspect }) {
  const [start, setStart] = useState('')
  const [end, setEnd] = useState('')
  const [pair, setPair] = useState(null)
  const [error, setError] = useState('')
  const [asset, setAsset] = useState('all')
  const edges = graph?.edges.map(edge => asset === 'all' ? edge : {
    ...edge,
    transfer_count: asset === 'eth' ? edge.native_transfers : edge.token_transfers,
    unknown_receipts: asset === 'eth' ? edge.unknown_receipts : 0,
  })
  const path = pair ? findTransferPath(edges, ...pair) : []
  const example = edges?.find(edge => edge.transfer_count > 0 && edge.sender !== edge.recipient)

  function trace(event) {
    event.preventDefault()
    const from = walletAddress(start)
    const to = walletAddress(end)
    if (!from || !to || from === to) {
      setError('Enter two different full Ethereum addresses.')
      setPair(null)
      return
    }
    setError('')
    setPair([from, to])
  }

  return <section className="panel transfer-trace" aria-labelledby="transfer-trace-heading">
    <div className="desk-heading"><div><h3 id="transfer-trace-heading">Trace wallet connections</h3><p>Find a directed path of up to four hops in the retained transfer sample.</p></div></div>
    {source !== 'live' ? <div className="trace-body">
      <p>Relationship tracing uses retained confirmed-chain transfers. Replay and predictions provide wallet observations.</p>
      <button type="button" onClick={onUseChain}>Use retained confirmed chain</button>
    </div> : <div className="trace-body">
      <form className="trace-form" onSubmit={trace}>
        <label>Starting wallet<input required maxLength={80} placeholder="0x… sender" value={start} onChange={event => { setStart(event.target.value); setPair(null); setError('') }} /></label>
        <label>Destination wallet<input required maxLength={80} placeholder="0x… recipient" value={end} onChange={event => { setEnd(event.target.value); setPair(null); setError('') }} /></label>
        <button className="primary-action" disabled={!graph}>Find transfer path</button>
        <label>Transfer type<select value={asset} onChange={event => setAsset(event.target.value)}>
          <option value="all">ETH and token transfers</option><option value="eth">ETH transfers only</option><option value="tokens">Token transfer logs only</option>
        </select></label>
        <button type="button" disabled={!start || !end} onClick={() => { setStart(end); setEnd(start); setPair(null); setError('') }}>Swap wallets</button>
        <button type="button" disabled={!example} onClick={() => {
          setStart(example.sender); setEnd(example.recipient)
          setPair([example.sender, example.recipient]); setError('')
        }}>Try a retained pair</button>
      </form>
      {error && <p className="desk-message" role="alert">{error}</p>}
      {!graph && <p className="desk-note">Loading the retained graph…</p>}
      {graph && !edges.some(edge => edge.transfer_count > 0) && <p className="desk-note">No positive-value transfers match this filter in the retained sample.</p>}
      {pair && graph && <div className="trace-results">
        <p role="status">{path.length ? `Shortest observed path: ${path.length} ${path.length === 1 ? 'hop' : 'hops'}.` : 'No directed path within four hops in this sample.'}</p>
        {path.length > 0 && <>
          <ol>{path.map(edge => <li key={edge.sender}>
            <button type="button" aria-label={`Inspect trace wallet ${edge.sender}`} title={edge.sender} onClick={() => onInspect(edge.sender)}>{shortWallet(edge.sender)}</button>
            <span>{edge.transfer_count} observed {edge.transfer_count === 1 ? 'transfer' : 'transfers'}{edge.unknown_receipts > 0 && ` · ${edge.unknown_receipts} with unknown receipt status`}</span>
          </li>)}<li><button type="button" aria-label={`Inspect trace wallet ${pair[1]}`} title={pair[1]} onClick={() => onInspect(pair[1])}>{shortWallet(pair[1])}</button><span>Destination</span></li></ol>
          <p className="desk-note">The hops show observed relationships. The sample may omit transfers, and the path does not establish timing, common ownership, or that the same funds moved through the wallets.</p>
        </>}
      </div>}
    </div>}
  </section>
}
