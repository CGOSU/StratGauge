import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import { ApiClientProvider } from './api/provider'
import App from './App'
import './styles.css'

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <ApiClientProvider>
      <App />
    </ApiClientProvider>
  </StrictMode>,
)
