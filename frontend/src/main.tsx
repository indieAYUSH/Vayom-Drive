import React from 'react'
import ReactDOM from 'react-dom/client'
import App from './App'
import './theme/tokens.css'

ReactDOM.createRoot(document.getElementById('root')!).render(
  <React.StrictMode>
    <App />
  </React.StrictMode>,
)

// Register Service Worker for virtual video streaming (update.md Section 3)
if (typeof window !== 'undefined' && 'serviceWorker' in navigator) {
  window.addEventListener('load', () => {
    navigator.serviceWorker.register('/sw.js').catch(() => {
      // Graceful fallback: direct HTTP streaming still active
    })
  })
}
