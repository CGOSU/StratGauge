import { BarChart3, Briefcase, ChartLine, Eye, LayoutDashboard, ListChecks, Receipt, Settings2, Upload } from 'lucide-react'
import type { HealthState, AppRoute } from '@/types'


export const appRouteLabels: Record<AppRoute, string> = {
  workbench: '工作台',
  signals: '今日信号',
  watchlist: '观察池',
  positions: '持仓',
  trades: '交易记录',
  strategies: '策略',
  analytics: '分析',
  import: '导入数据',
  settings: '设置',
}

export const appRouteIcons: Record<AppRoute, typeof LayoutDashboard> = {
  workbench: LayoutDashboard,
  signals: ListChecks,
  watchlist: Eye,
  positions: Briefcase,
  trades: Receipt,
  strategies: BarChart3,
  analytics: ChartLine,
  import: Upload,
  settings: Settings2,
}

export const primaryAppRoutes: AppRoute[] = [
  'workbench',
  'signals',
  'watchlist',
  'positions',
  'trades',
  'strategies',
  'analytics',
]
export function AppSidebar({
  activeRoute,
  onNavigate,
}: {
  activeRoute: AppRoute
  onNavigate: (route: AppRoute) => void
}) {
  return (
    <aside className="app-sidebar" aria-label="主导航">
      <div className="app-brand">
        <div className="app-brand-mark" aria-hidden="true">SG</div>
        <div>
          <strong>StratGauge</strong>
          <span>策衡研究台</span>
        </div>
      </div>
      <div className="app-sidebar-section-label">研究工作区</div>
      <nav className="app-sidebar-nav">
        {primaryAppRoutes.map((route) => {
          const Icon = appRouteIcons[route]
          return (
            <button
              type="button"
              className={`app-nav-item${activeRoute === route ? ' app-nav-item--active' : ''}`}
              aria-label={appRouteLabels[route]}
              aria-current={activeRoute === route ? 'page' : undefined}
              key={route}
              title={appRouteLabels[route]}
              onClick={() => onNavigate(route)}
            >
              <Icon className="app-nav-icon" aria-hidden="true" />
              <span>{appRouteLabels[route]}</span>
            </button>
          )
        })}
      </nav>
      <div className="app-sidebar-spacer" />
      <div className="app-sidebar-section-label">低频管理</div>
      <button
        type="button"
        className={`app-nav-item${activeRoute === 'import' ? ' app-nav-item--active' : ''}`}
        aria-label={appRouteLabels.import}
        aria-current={activeRoute === 'import' ? 'page' : undefined}
        onClick={() => onNavigate('import')}
        title={appRouteLabels.import}
      >
        <Upload className="app-nav-icon" aria-hidden="true" />
        <span>导入数据</span>
      </button>
      <button
        type="button"
        className={`app-nav-item${activeRoute === 'settings' ? ' app-nav-item--active' : ''}`}
        aria-label={appRouteLabels.settings}
        aria-current={activeRoute === 'settings' ? 'page' : undefined}
        onClick={() => onNavigate('settings')}
        title={appRouteLabels.settings}
      >
        <Settings2 className="app-nav-icon" aria-hidden="true" />
        <span>设置</span>
      </button>
      <div className="app-sidebar-footer">
        <span className="app-sidebar-status-dot" aria-hidden="true" />
        <span>人工确认模式</span>
      </div>
    </aside>
  )
}
export function OperationalStatusBar({
  healthState,
  activeRoute,
}: {
  healthState: HealthState
  activeRoute: AppRoute
}) {
  return (
    <header className="operational-bar">
      <div className="operational-context">
        <span className="operational-kicker">研究工作台</span>
        <strong>{appRouteLabels[activeRoute]}</strong>
      </div>
      <div className="operational-statuses">
        <span className="operational-status operational-status--evidence">
          <span className="operational-status-dot" aria-hidden="true" />
          本地证据优先
        </span>
        <span className={`operational-status operational-status--${healthState}`} role="status" aria-live="polite">
          <span className="operational-status-dot" aria-hidden="true" />
          API {healthState === 'checking' ? '检测中' : healthState === 'online' ? '在线' : '离线'}
        </span>
        <span className="operational-status operational-status--manual">不自动下单</span>
      </div>
    </header>
  )
}
