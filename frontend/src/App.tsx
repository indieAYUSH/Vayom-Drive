import React, { useEffect } from 'react'
import { Shell } from './components/layout/Shell'
import { LoginView } from './views/LoginView'
import { useAuthStore } from './store/useAuthStore'

export const App: React.FC = () => {
  const { isAuthenticated, isLoading, checkAuth } = useAuthStore()

  useEffect(() => {
    checkAuth()
  }, [checkAuth])

  if (isLoading) {
    return (
      <div className="min-h-screen bg-[#000000] flex flex-col items-center justify-center font-mono text-xs text-[#888888] select-none">
        <div className="w-8 h-8 border-2 border-[#333333] border-t-[#ffffff] animate-spin mb-4" />
        <span className="tracking-widest uppercase text-[#cccccc]">VAYOM DRIVE</span>
        <span className="text-[10px] text-[#555555] mt-1">VERIFYING NODE GATEWAY...</span>
      </div>
    )
  }

  if (!isAuthenticated) {
    return <LoginView />
  }

  return <Shell />
}

export default App
