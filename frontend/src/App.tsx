import { useEffect, useRef, useState } from 'react'

type HealthState = 'checking' | 'online' | 'offline'

const healthUrl = import.meta.env.VITE_API_BASE_URL
  ? `${import.meta.env.VITE_API_BASE_URL.replace(/\/$/, '')}/healthz`
  : '/api/healthz'

function App() {
  const [healthState, setHealthState] = useState<HealthState>('checking')
  const healthCheckStarted = useRef(false)

  useEffect(() => {
    if (healthCheckStarted.current) {
      return
    }
    healthCheckStarted.current = true
    const controller = new AbortController()

    void fetch(healthUrl, { signal: controller.signal })
      .then((response) => {
        if (!response.ok) {
          throw new Error(`Health check failed: ${response.status}`)
        }
        setHealthState('online')
      })
      .catch((error: unknown) => {
        if (error instanceof DOMException && error.name === 'AbortError') {
          return
        }
        setHealthState('offline')
      })

    return () => controller.abort()
  }, [])

  return (
    <main className="shell">
      <header className="hero">
        <p className="eyebrow">STRATGAUGE · 策衡</p>
        <h1>衡量优势，以证据交易。</h1>
        <p className="summary">
          可信数据、保守回测和人工确认，是每日策略工作的起点。
        </p>
      </header>

      <section className="status-card" aria-labelledby="status-title">
        <div>
          <p className="eyebrow">阶段 0</p>
          <h2 id="status-title">可信基础正在建设</h2>
          <p>后端领域模型、数据适配器、快照质量门和下一交易日回测已具备。</p>
        </div>
        <div className="status-actions">
          <span className="status-badge">仅手动交易</span>
          <span
            className={`health-status health-status--${healthState}`}
            role="status"
            aria-live="polite"
          >
            API：{healthState === 'checking' ? '检测中' : healthState === 'online' ? '在线' : '离线'}
          </span>
        </div>
      </section>

      <section className="principles" aria-labelledby="principles-title">
        <h2 id="principles-title">当前边界</h2>
        <ul>
          <li>收盘后生成信号，最早下一交易日执行。</li>
          <li>数据质量失败时不发布推荐。</li>
          <li>系统不连接券商，也不会自动下单。</li>
        </ul>
      </section>
    </main>
  )
}

export default App
