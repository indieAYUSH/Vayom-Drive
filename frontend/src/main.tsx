import React, { Component, ErrorInfo, ReactNode } from 'react'
import ReactDOM from 'react-dom/client'
import App from './App'
import './theme/tokens.css'

interface Props {
  children: ReactNode
}

interface State {
  hasError: boolean
  error: Error | null
}

class RootErrorBoundary extends Component<Props, State> {
  public state: State = {
    hasError: false,
    error: null,
  }

  public static getDerivedStateFromError(error: Error): State {
    return { hasError: true, error }
  }

  public componentDidCatch(error: Error, errorInfo: ErrorInfo) {
    console.error('Unhandled UI exception:', error, errorInfo)
  }

  public render() {
    if (this.state.hasError) {
      return (
        <div className="min-h-screen w-screen bg-[#000000] text-[#f0f0f0] flex flex-col items-center justify-center p-6 text-center select-none">
          <div className="w-12 h-12 border border-[#ef4444] text-[#ef4444] flex items-center justify-center mb-4 text-xl font-mono">
            !
          </div>
          <h1 className="text-base font-semibold tracking-wide uppercase text-[#f0f0f0]">
            Workspace Display Recovery
          </h1>
          <p className="text-xs text-[#888888] max-w-md mt-2 font-mono">
            {this.state.error?.message || 'An unexpected rendering issue occurred.'}
          </p>
          <button
            onClick={() => {
              this.setState({ hasError: false, error: null })
              window.location.reload()
            }}
            className="mt-6 px-4 py-2 bg-[#ffffff] text-[#000000] text-xs font-semibold hover:bg-[#e5e5e5] transition-colors"
          >
            Reload Workspace
          </button>
        </div>
      )
    }

    return this.props.children
  }
}

ReactDOM.createRoot(document.getElementById('root')!).render(
  <React.StrictMode>
    <RootErrorBoundary>
      <App />
    </RootErrorBoundary>
  </React.StrictMode>,
)
