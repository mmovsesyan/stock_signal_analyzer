import { useEffect, useMemo, useState } from 'react'

const API_BASE = window.location.origin

const COLUMNS = [
  { key: 'symbol', label: 'Symbol', width: '100px' },
  { key: 'company', label: 'Company', width: '180px' },
  { key: 'score', label: 'Score', width: '80px' },
  { key: 'signal_tier', label: 'Tier', width: '60px' },
  { key: 'direction', label: 'Dir', width: '60px' },
  { key: 'confidence', label: 'Conf', width: '70px' },
  { key: 'verdict', label: 'Verdict', width: '140px' },
  { key: 'technical_score', label: 'Tech', width: '70px' },
  { key: 'momentum_score', label: 'Mom', width: '70px' },
  { key: 'news_score', label: 'News', width: '70px' },
  { key: 'volume_score', label: 'Vol', width: '70px' },
]

function scoreColor(score) {
  if (score >= 0.3) return '#22c55e'
  if (score >= 0.1) return '#84cc16'
  if (score >= -0.1) return '#94a3b8'
  if (score >= -0.3) return '#f59e0b'
  return '#ef4444'
}

function tierBadge(tier) {
  const colors = { A: '#22c55e', B: '#3b82f6', C: '#f59e0b' }
  return colors[tier] || '#94a3b8'
}

export default function ScreenerTable() {
  const [data, setData] = useState([])
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState(null)
  const [market, setMarket] = useState('all')
  const [sortKey, setSortKey] = useState('score')
  const [sortDesc, setSortDesc] = useState(true)

  const fetchScreen = async () => {
    setLoading(true)
    setError(null)
    try {
      const res = await fetch(`${API_BASE}/screen?market=${market}&max_results=50&fast=true`)
      if (!res.ok) throw new Error(`HTTP ${res.status}`)
      const json = await res.json()
      setData(json.results || [])
    } catch (e) {
      setError(e.message)
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    fetchScreen()
  }, [market])

  const sorted = useMemo(() => {
    const arr = [...data]
    arr.sort((a, b) => {
      const av = a[sortKey] ?? 0
      const bv = b[sortKey] ?? 0
      if (typeof av === 'string' && typeof bv === 'string') {
        return sortDesc ? bv.localeCompare(av) : av.localeCompare(bv)
      }
      return sortDesc ? bv - av : av - bv
    })
    return arr
  }, [data, sortKey, sortDesc])

  const handleSort = (key) => {
    if (sortKey === key) {
      setSortDesc((d) => !d)
    } else {
      setSortKey(key)
      setSortDesc(true)
    }
  }

  return (
    <div>
      <div style={{ display: 'flex', gap: '12px', marginBottom: '16px', alignItems: 'center' }}>
        <select
          value={market}
          onChange={(e) => setMarket(e.target.value)}
          style={{ padding: '6px 10px', borderRadius: '6px', border: '1px solid #334155', background: '#1e293b', color: '#e2e8f0' }}
        >
          <option value="all">All Markets</option>
          <option value="us">US Blue Chips</option>
          <option value="ru">RU Blue Chips</option>
        </select>
        <button
          onClick={fetchScreen}
          disabled={loading}
          style={{ padding: '6px 14px', borderRadius: '6px', border: 'none', background: '#2563eb', color: '#fff', cursor: 'pointer' }}
        >
          {loading ? 'Scanning...' : 'Run Screen'}
        </button>
        <span style={{ color: '#94a3b8', fontSize: '0.85rem' }}>
          {data.length} results
        </span>
      </div>

      {error && (
        <div style={{ padding: '10px 12px', background: '#450a0a', color: '#fca5a5', borderRadius: '6px', marginBottom: '12px' }}>
          Error: {error}
        </div>
      )}

      <div style={{ overflowX: 'auto', border: '1px solid #1e293b', borderRadius: '8px' }}>
        <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: '0.85rem' }}>
          <thead>
            <tr style={{ background: '#1e293b' }}>
              {COLUMNS.map((col) => (
                <th
                  key={col.key}
                  onClick={() => handleSort(col.key)}
                  style={{
                    width: col.width,
                    padding: '10px 8px',
                    textAlign: 'left',
                    cursor: 'pointer',
                    userSelect: 'none',
                    borderBottom: '1px solid #334155',
                    color: '#93c5fd',
                  }}
                >
                  {col.label} {sortKey === col.key ? (sortDesc ? '▼' : '▲') : ''}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {sorted.map((row) => (
              <tr key={row.symbol} style={{ borderBottom: '1px solid #1e293b' }}>
                <td style={{ padding: '8px', fontWeight: 600 }}>{row.symbol}</td>
                <td style={{ padding: '8px', color: '#cbd5e1' }}>{row.company}</td>
                <td style={{ padding: '8px', fontWeight: 700, color: scoreColor(row.score) }}>{row.score > 0 ? '+' : ''}{row.score.toFixed(2)}</td>
                <td style={{ padding: '8px' }}>
                  <span
                    style={{
                      display: 'inline-block',
                      padding: '2px 8px',
                      borderRadius: '4px',
                      background: tierBadge(row.signal_tier),
                      color: '#0f172a',
                      fontWeight: 700,
                      fontSize: '0.75rem',
                    }}
                  >
                    {row.signal_tier}
                  </span>
                </td>
                <td style={{ padding: '8px', color: row.direction === 'long' ? '#22c55e' : row.direction === 'short' ? '#ef4444' : '#94a3b8' }}>
                  {row.direction}
                </td>
                <td style={{ padding: '8px' }}>{(row.confidence * 100).toFixed(0)}%</td>
                <td style={{ padding: '8px', maxWidth: '140px', whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>{row.verdict}</td>
                <td style={{ padding: '8px' }}>{row.technical_score > 0 ? '+' : ''}{row.technical_score.toFixed(2)}</td>
                <td style={{ padding: '8px' }}>{row.momentum_score > 0 ? '+' : ''}{row.momentum_score.toFixed(2)}</td>
                <td style={{ padding: '8px' }}>{row.news_score > 0 ? '+' : ''}{row.news_score.toFixed(2)}</td>
                <td style={{ padding: '8px' }}>{row.volume_score > 0 ? '+' : ''}{row.volume_score.toFixed(2)}</td>
              </tr>
            ))}
          </tbody>
        </table>
        {sorted.length === 0 && !loading && (
          <div style={{ padding: '24px', textAlign: 'center', color: '#64748b' }}>No signals found.</div>
        )}
      </div>
    </div>
  )
}
