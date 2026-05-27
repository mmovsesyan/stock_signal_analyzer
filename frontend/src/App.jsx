import ScreenerTable from './ScreenerTable.jsx'

function App() {
  return (
    <div style={{ padding: '24px', maxWidth: '1400px', margin: '0 auto' }}>
      <h1 style={{ fontSize: '1.5rem', marginBottom: '16px', color: '#93c5fd' }}>
        Stock Signal Analyzer — Screener
      </h1>
      <ScreenerTable />
    </div>
  )
}

export default App
