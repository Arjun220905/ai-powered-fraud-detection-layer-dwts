import { useState } from 'react'

export default function WalletGraph({ graph, onInspect }) {
  const [draftQuery, setDraftQuery] = useState('')
  const [query, setQuery] = useState('')
  const [risk, setRisk] = useState('all')
  const [page, setPage] = useState(0)
  const [zoom, setZoom] = useState(1)
  const [center, setCenter] = useState({ x: 300, y: 220 })
  const [drag, setDrag] = useState(null)
  const matching = (graph?.nodes || []).filter(node => node.address.toLowerCase().includes(query.trim().toLowerCase()) &&
    (risk === 'all' || (risk === 'high' ? node.probability >= .8 : node.probability == null)))
  const pages = Math.max(1, Math.ceil(matching.length / 24))
  const currentPage = Math.min(page, pages - 1)
  const nodes = matching.slice(currentPage * 24, (currentPage + 1) * 24)
  const positions = Object.fromEntries(nodes.map((node, i) => [node.address, { x: 300 + 215 * Math.cos(i * 2 * Math.PI / nodes.length), y: 220 + 150 * Math.sin(i * 2 * Math.PI / nodes.length) }]))
  function resetView() { setZoom(1); setCenter({ x: 300, y: 220 }) }
  function changeFilter(next) { next(); setPage(0); resetView() }
  function search(event) {
    event.preventDefault()
    changeFilter(() => setQuery(draftQuery.trim()))
  }
  return <>
    <div className="graph-tools">
      <form className="graph-search" role="search" onSubmit={search}>
        <label htmlFor="wallet-graph-search">Find wallet</label>
        <div className="graph-search-row"><input id="wallet-graph-search" value={draftQuery} placeholder="Full or partial wallet address" onChange={event => setDraftQuery(event.target.value)}/><button className="primary-action" type="submit">Search graph</button></div>
      </form>
      <label className="graph-filter">Show wallets<select aria-label="Show wallets" value={risk} onChange={event => changeFilter(() => setRisk(event.target.value))}><option value="all">All risk levels</option><option value="high">High risk (80% or more)</option><option value="unknown">Not yet scored</option></select></label>
      <div className="graph-view"><span>View controls</span><div className="graph-zoom"><button type="button" aria-label="Zoom out" disabled={zoom <= 1} onClick={() => setZoom(value => Math.max(1, value - .5))}>−</button><button type="button" aria-label="Zoom in" disabled={zoom >= 4} onClick={() => setZoom(value => Math.min(4, value + .5))}>+</button><button type="button" onClick={resetView}>Reset view</button></div></div>
    </div>
    {nodes.length ? <svg className="wallet-graph" role="group" tabIndex="0" aria-label="Wallet connections; use arrow keys to pan when zoomed" viewBox={`${center.x - 300 / zoom} ${center.y - 220 / zoom} ${600 / zoom} ${440 / zoom}`} style={{ touchAction: zoom > 1 ? 'none' : 'auto' }}
      onKeyDown={e => { if (e.target === e.currentTarget && zoom > 1 && ['ArrowLeft', 'ArrowRight', 'ArrowUp', 'ArrowDown'].includes(e.key)) { e.preventDefault(); setCenter(c => ({ x: c.x + (e.key === 'ArrowLeft' ? -30 : e.key === 'ArrowRight' ? 30 : 0) / zoom, y: c.y + (e.key === 'ArrowUp' ? -30 : e.key === 'ArrowDown' ? 30 : 0) / zoom })); } }}
      onPointerDown={e => { if (zoom > 1 && !e.target.closest('[role="button"]')) { e.currentTarget.setPointerCapture(e.pointerId); setDrag({ x: e.clientX, y: e.clientY, center }); } }}
      onPointerMove={e => { if (drag) { const bounds = e.currentTarget.getBoundingClientRect(); const scale = Math.max(600 / bounds.width, 440 / bounds.height) / zoom; setCenter({ x: drag.center.x - (e.clientX - drag.x) * scale, y: drag.center.y - (e.clientY - drag.y) * scale }); } }}
      onPointerUp={() => setDrag(null)} onPointerCancel={() => setDrag(null)}>
      <defs><marker id="wallet-arrow" viewBox="0 0 10 10" refX="20" refY="5" markerWidth="5" markerHeight="5" orient="auto"><path d="M0 0 10 5 0 10Z" fill="var(--text-muted)"/></marker></defs>
      {(graph.edges || []).filter(edge => positions[edge.sender] && positions[edge.recipient]).map(edge => <line key={`${edge.sender}-${edge.recipient}`} x1={positions[edge.sender].x} y1={positions[edge.sender].y} x2={positions[edge.recipient].x} y2={positions[edge.recipient].y} stroke="var(--line-strong)" markerEnd="url(#wallet-arrow)"><title>{edge.sender} to {edge.recipient}: {edge.count} transfers</title></line>)}
      {nodes.map(node => <g key={node.address} role="button" tabIndex="0" aria-label={`Investigate ${node.address}`} onClick={() => onInspect(node.address)} onKeyDown={e => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); onInspect(node.address) } }}>
        <circle cx={positions[node.address].x} cy={positions[node.address].y} r="10" fill={node.probability == null ? 'var(--text-muted)' : node.probability >= .8 ? 'var(--danger)' : 'var(--trust-accent)'}/>
        <text x={positions[node.address].x} y={positions[node.address].y - 18} textAnchor="middle" fill="var(--text-secondary)" fontSize="11">{node.address.slice(0, 6)}…{node.address.slice(-4)}</text><title>{node.address}</title>
      </g>)}
    </svg> : <div className="empty-state"><strong>{graph?.nodes.length ? 'No matching wallets' : 'No transfers recorded yet'}</strong><p>{graph?.nodes.length ? 'Try a shorter address or choose all risk levels.' : 'Connect an Ethereum provider and start Confirmed chain to see wallet connections.'}</p></div>}
    <div className="pagination"><button disabled={currentPage === 0} onClick={() => { setPage(currentPage - 1); resetView() }}>Previous wallets</button><span>{matching.length} matches · page {currentPage + 1} of {pages}</span><button disabled={currentPage + 1 >= pages} onClick={() => { setPage(currentPage + 1); resetView() }}>Next wallets</button></div>
    <p className="intel-note">Arrows show transfers between wallets on this page. Red: high risk. Gray: not scored. Zoom: {Math.round(zoom * 100)}%. Drag to pan when zoomed; select a wallet to investigate.</p>
  </>
}
