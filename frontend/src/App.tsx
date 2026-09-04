import { useEffect, useState } from 'react'
import { apiBase } from '@/lib/api'
import { healthUrl } from '@/api/client'
import { formatManagementDate, formatPrice } from '@/lib/format'
import { appRouteLabels, AppSidebar, OperationalStatusBar } from '@/components/app-shell'
import { WorkbenchWorkspace } from '@/components/workbench/workspace'
import { DataManagementWorkspace } from '@/components/management/workspace'
import { ImportWorkspace } from '@/components/import-workspace'
import { PortfolioWorkspace } from '@/components/portfolio-workspace'
import { useImportData } from '@/hooks/use-import-data'
import type { AppRoute, HealthState, WorkbenchRoute, Workspace } from '@/types'


function App() {
  const [workspace, setWorkspace] = useState<Workspace>('research')
  const [activeRoute, setActiveRoute] = useState<AppRoute>('signals')
  const [healthState, setHealthState] = useState<HealthState>('checking')

  useEffect(() => {
    const controller = new AbortController()

    void fetch(healthUrl, { signal: controller.signal })
      .then((response) => {
        if (!response.ok) {
          throw new Error(`Health check failed: ${response.status}`)
        }
        setHealthState('online')
      })
      .catch((reason: unknown) => {
        if (reason instanceof DOMException && reason.name === 'AbortError') {
          return
        }
        setHealthState('offline')
      })

    return () => controller.abort()
  }, [])

  const data = useImportData(apiBase, activeRoute === 'import')

  function navigateTo(route: AppRoute) {
    setActiveRoute(route)
    if (route === 'settings') {
      setWorkspace('management')
    } else if (route === 'positions' || route === 'trades') {
      setWorkspace('portfolio')
    } else {
      setWorkspace('research')
    }
  }

  return (
    <main className="app-shell">
      <AppSidebar activeRoute={activeRoute} onNavigate={navigateTo} />
      <div className="app-main-column">
        <OperationalStatusBar healthState={healthState} activeRoute={activeRoute} />
        <section className="app-content-shell">
          <header className="content-page-heading">
            <div>
              <p className="eyebrow">STRATGAUGE · 策衡</p>
              <h1>{appRouteLabels[activeRoute]}</h1>
              <p className="content-page-summary">
                {activeRoute === 'settings'
                  ? '公式、标签和批次等低频管理。'
                  : activeRoute === 'import'
                    ? '导入通达信收盘后结果，保留原始文件证据并创建选股批次。'
                    : '从策略信号出发，保留人工判断、客观行情与真实交易的证据边界。'}
              </p>
            </div>
            <div className="content-page-meta">
              <span>证据驱动</span>
              <span>不自动交易</span>
            </div>
          </header>

      {activeRoute !== 'settings' && activeRoute !== 'import' ? (
        <WorkbenchWorkspace apiBase={apiBase} route={activeRoute as WorkbenchRoute} />
      ) : workspace === 'management' ? (
        <DataManagementWorkspace
          apiBase={apiBase}
          onFormulaCreated={() => {
            if (activeRoute === 'import') {
              void data.loadFormulas()
            }
          }}
        />
      ) : workspace === 'portfolio' ? (
        <PortfolioWorkspace
          apiBase={apiBase}
          formatDate={formatManagementDate}
          formatPrice={formatPrice}
        />
      ) : (
        <ImportWorkspace data={data} healthState={healthState} />
      )}
        </section>
      </div>
    </main>
  )
}


export default App
