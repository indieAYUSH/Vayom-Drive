import React, { useState, useEffect, useRef } from 'react'
import { QRCodeSVG } from 'qrcode.react'
import { useAuthStore } from '../store/useAuthStore'
import {
  Shield,
  QrCode,
  Bot,
  Terminal,
  RefreshCw,
  Key,
  ExternalLink,
  ArrowRight,
  CheckCircle2,
  AlertTriangle,
  Edit3
} from 'lucide-react'
import { Button } from '../components/common/Button'
import { Badge } from '../components/common/Badge'

type TabType = 'byok_qr' | 'bot' | 'qr' | 'developer'

export const LoginView: React.FC = () => {
  const {
    startQrSession,
    qrUrl,
    qrStatus,
    qrExpiresAt,
    scannedUser,
    availableChannels,
    defaultChannelId,
    submit2FA,
    finalizeQr,
    loginWithBot,
    loginWithDeveloper,
    error,
    clearError,
    isLoading
  } = useAuthStore()

  // Primary method default: BYOK + QR Scan (Anti-Ban)
  const [activeTab, setActiveTab] = useState<TabType>('byok_qr')

  // BYOK Credentials state (persisted in localStorage for convenience)
  const [byokApiId, setByokApiId] = useState<string>(() => {
    try {
      return localStorage.getItem('vayom_byok_api_id') || ''
    } catch {
      return ''
    }
  })
  const [byokApiHash, setByokApiHash] = useState<string>(() => {
    try {
      return localStorage.getItem('vayom_byok_api_hash') || ''
    } catch {
      return ''
    }
  })
  const [editingByokCreds, setEditingByokCreds] = useState<boolean>(false)

  // Form states for QR Finalize
  const [displayName, setDisplayName] = useState('')
  const [selectedChannelAction, setSelectedChannelAction] = useState<'auto_create' | 'select_existing'>('auto_create')
  const [selectedChannelId, setSelectedChannelId] = useState('')
  const [twoFactorPassword, setTwoFactorPassword] = useState('')

  // Form states for Bot Token
  const [botToken, setBotToken] = useState('')
  const [botChannelId, setBotChannelId] = useState('')
  const [botDisplayName, setBotDisplayName] = useState('')

  // Form states for Developer Mode
  const [devApiId, setDevApiId] = useState('')
  const [devApiHash, setDevApiHash] = useState('')
  const [devBotToken, setDevBotToken] = useState('')
  const [devChannelId, setDevChannelId] = useState('')
  const [devDisplayName, setDevDisplayName] = useState('')

  // Countdown timer for QR (5 minutes = 300s)
  const [timeLeft, setTimeLeft] = useState<number>(0)

  const formatTime = (seconds: number) => {
    const mins = Math.floor(seconds / 60)
    const secs = seconds % 60
    return `${mins.toString().padStart(2, '0')}:${secs.toString().padStart(2, '0')}`
  }

  // Guard against duplicate / retry storms for QR session initiation
  const hasAttemptedRef = useRef(false)

  // Quick QR (Shared) automatic trigger when switched to Tab 3
  useEffect(() => {
    if (activeTab === 'qr' && qrStatus === 'idle' && !hasAttemptedRef.current) {
      hasAttemptedRef.current = true
      startQrSession()
    }
  }, [activeTab, qrStatus, startQrSession])

  useEffect(() => {
    if (activeTab !== 'qr') {
      hasAttemptedRef.current = false
    }
  }, [activeTab])

  useEffect(() => {
    if (!qrExpiresAt) return
    const updateCountdown = () => {
      const now = Math.floor(Date.now() / 1000)
      const diff = Math.max(0, qrExpiresAt - now)
      setTimeLeft(diff)
    }
    updateCountdown()
    const timer = setInterval(updateCountdown, 1000)
    return () => clearInterval(timer)
  }, [qrExpiresAt])

  useEffect(() => {
    if (scannedUser) {
      setDisplayName(scannedUser.first_name || scannedUser.display_name || 'Vault User')
      if (defaultChannelId) {
        setSelectedChannelAction('select_existing')
        setSelectedChannelId(defaultChannelId)
      } else if (availableChannels.length > 0) {
        setSelectedChannelAction('select_existing')
        setSelectedChannelId(availableChannels[0].id)
      } else {
        setSelectedChannelAction('auto_create')
      }
    }
  }, [scannedUser, defaultChannelId, availableChannels])

  const handleStartByokQr = (e?: React.FormEvent) => {
    if (e) e.preventDefault()
    const trimmedId = byokApiId.trim()
    const trimmedHash = byokApiHash.trim()
    const numId = Number(trimmedId)

    if (!trimmedId || isNaN(numId) || numId <= 0) {
      useAuthStore.setState({ error: 'Please enter a valid numeric Telegram API ID.' })
      return
    }
    if (!trimmedHash) {
      useAuthStore.setState({ error: 'Please enter your Telegram API Hash (from my.telegram.org).' })
      return
    }

    try {
      localStorage.setItem('vayom_byok_api_id', trimmedId)
      localStorage.setItem('vayom_byok_api_hash', trimmedHash)
    } catch {
      // ignore
    }

    clearError()
    setEditingByokCreds(false)
    startQrSession({ apiId: numId, apiHash: trimmedHash })
  }

  const handle2FASubmit = async (e: React.FormEvent) => {
    e.preventDefault()
    if (!twoFactorPassword) return
    try {
      await submit2FA(twoFactorPassword)
    } catch {
      // surfaced via store error state
    }
  }

  const handleQrFinalize = async () => {
    try {
      await finalizeQr(
        displayName.trim() || 'Vault User',
        selectedChannelAction,
        selectedChannelAction === 'select_existing' ? selectedChannelId : undefined
      )
    } catch {
      // surfaced via store error state
    }
  }

  const handleBotSubmit = async (e: React.FormEvent) => {
    e.preventDefault()
    if (!botToken || !botChannelId) return
    try {
      await loginWithBot(botToken, botChannelId, botDisplayName)
    } catch {
      // surfaced via store error state
    }
  }

  const handleDeveloperSubmit = async (e: React.FormEvent) => {
    e.preventDefault()
    if (!devApiId || !devApiHash || !devBotToken || !devChannelId) return
    try {
      await loginWithDeveloper(Number(devApiId), devApiHash, devBotToken, devChannelId, devDisplayName)
    } catch {
      // surfaced via store error state
    }
  }

  return (
    <div className="min-h-screen bg-[#000000] text-[#e5e5e5] flex flex-col justify-between selection:bg-[#ffffff] selection:text-[#000000]">
      {/* Top Navigation Bar */}
      <header className="h-14 border-b border-[#222222] px-4 sm:px-6 flex items-center justify-between bg-[#080808]">
        <div className="flex items-center gap-3">
          <div className="w-5 h-5 bg-[#ffffff] flex items-center justify-center">
            <div className="w-2.5 h-2.5 bg-[#000000]" />
          </div>
          <span className="text-sm font-bold tracking-wider text-[#ffffff] uppercase">
            VAYOM DRIVE
          </span>
          <span className="text-[#444444] hidden sm:inline">|</span>
          <span className="text-xs text-[#888888] hidden sm:inline font-mono">NODE GATEWAY</span>
        </div>

        <div className="flex items-center gap-2">
          <span className="w-2 h-2 rounded-full bg-[#f59e0b] animate-pulse" />
          <span className="text-xs text-[#f59e0b] font-medium">
            Authentication Required
          </span>
        </div>
      </header>

      {/* Main Content Portal */}
      <main className="flex-1 flex items-center justify-center p-4 sm:p-6 my-4">
        <div className="w-full max-w-xl bg-[#0a0a0a] border border-[#282828] shadow-none">
          {/* Method Selector Tabs */}
          <div className="grid grid-cols-2 sm:grid-cols-4 border-b border-[#282828] text-xs font-medium">
            {/* TAB 1: BYOK + QR (PRIMARY / RECOMMENDED) */}
            <button
              onClick={() => { setActiveTab('byok_qr'); clearError(); }}
              className={`p-3 text-center border-r border-[#282828] transition-colors flex items-center justify-center gap-1.5 ${
                activeTab === 'byok_qr'
                  ? 'bg-[#141414] text-[#ffffff] border-b-2 border-b-[#22c55e]'
                  : 'text-[#888888] hover:text-[#cccccc] hover:bg-[#0d0d0d]'
              }`}
            >
              <Shield size={14} className={activeTab === 'byok_qr' ? 'text-[#22c55e]' : ''} />
              <span className="truncate">BYOK + QR</span>
            </button>

            {/* TAB 2: BOT & CHANNEL (PRIMARY / ISOLATED) */}
            <button
              onClick={() => { setActiveTab('bot'); clearError(); }}
              className={`p-3 text-center border-r border-[#282828] transition-colors flex items-center justify-center gap-1.5 ${
                activeTab === 'bot'
                  ? 'bg-[#141414] text-[#ffffff] border-b-2 border-b-[#22c55e]'
                  : 'text-[#888888] hover:text-[#cccccc] hover:bg-[#0d0d0d]'
              }`}
            >
              <Bot size={14} className={activeTab === 'bot' ? 'text-[#22c55e]' : ''} />
              <span className="truncate">Bot & Channel</span>
            </button>

            {/* TAB 3: QUICK QR (SECONDARY / SHARED) */}
            <button
              onClick={() => { setActiveTab('qr'); clearError(); }}
              className={`p-3 text-center border-r border-[#282828] transition-colors flex items-center justify-center gap-1.5 ${
                activeTab === 'qr'
                  ? 'bg-[#141414] text-[#ffffff] border-b-2 border-b-[#f59e0b]'
                  : 'text-[#888888] hover:text-[#cccccc] hover:bg-[#0d0d0d]'
              }`}
            >
              <QrCode size={14} className={activeTab === 'qr' ? 'text-[#f59e0b]' : ''} />
              <span className="truncate">Quick QR (Shared)</span>
            </button>

            {/* TAB 4: DEVELOPER NODE */}
            <button
              onClick={() => { setActiveTab('developer'); clearError(); }}
              className={`p-3 text-center transition-colors flex items-center justify-center gap-1.5 ${
                activeTab === 'developer'
                  ? 'bg-[#141414] text-[#ffffff] border-b-2 border-b-[#ffffff]'
                  : 'text-[#888888] hover:text-[#cccccc] hover:bg-[#0d0d0d]'
              }`}
            >
              <Terminal size={14} className={activeTab === 'developer' ? 'text-[#ffffff]' : ''} />
              <span className="truncate">Developer</span>
            </button>
          </div>

          {/* Global Error Banner */}
          {error && (
            <div className="p-3 bg-[#1f0a0a] border-b border-[#7f1d1d] text-xs text-[#ef4444] flex items-center justify-between">
              <span>{error}</span>
              <button onClick={clearError} className="text-[#999999] hover:text-[#ffffff] text-sm">✕</button>
            </div>
          )}

          {/* TAB 1: BYOK + QR SCAN (PRIMARY / RECOMMENDED ANTI-BAN) */}
          {activeTab === 'byok_qr' && (
            <div className="p-6 space-y-6">
              <div className="flex items-center justify-between pb-3 border-b border-[#1c1c1c]">
                <div>
                  <h2 className="text-sm font-semibold tracking-normal text-[#ffffff] flex items-center gap-2">
                    Sovereign BYOK + QR Scan
                  </h2>
                  <p className="text-xs text-[#888888] mt-0.5">
                    Your personal API keys • Zero shared-app ban risk • 4 GB Premium support
                  </p>
                </div>
                <Badge variant="success">PRIMARY // ANTI-BAN</Badge>
              </div>

              {/* Anti-Ban Architecture Information Banner */}
              <div className="p-3 bg-[#0a1a0f] border border-[#14532d] text-xs text-[#86efac] flex items-start gap-2.5">
                <Shield size={16} className="text-[#22c55e] flex-shrink-0 mt-0.5" />
                <div className="space-y-1">
                  <span className="font-semibold text-[#ffffff] block">Zero Ban Risk Guarantee</span>
                  <p className="text-[11px] text-[#bbf7d0] leading-relaxed">
                    By bringing your own API keys, your cloud traffic connects to Telegram under your private developer application rather than a shared gateway. This fully safeguards your account against third-party rate limits and app bans.
                  </p>
                  <a
                    href="https://my.telegram.org"
                    target="_blank"
                    rel="noreferrer"
                    className="inline-flex items-center gap-1 text-[11px] text-[#22c55e] hover:underline font-medium pt-0.5"
                  >
                    Get free API ID & Hash in 30 seconds at my.telegram.org <ExternalLink size={10} />
                  </a>
                </div>
              </div>

              {/* BYOK Input Form (Shown when no active QR session or when editing credentials) */}
              {(!qrUrl || editingByokCreds || qrStatus === 'idle') && qrStatus !== 'confirmed' && qrStatus !== '2fa_required' && (
                <form onSubmit={handleStartByokQr} className="space-y-4">
                  <div className="grid grid-cols-1 sm:grid-cols-2 gap-3 text-xs">
                    <div className="space-y-1">
                      <label className="text-xs font-medium text-[#cccccc] block">
                        Telegram API ID *
                      </label>
                      <input
                        type="number"
                        value={byokApiId}
                        onChange={(e) => setByokApiId(e.target.value)}
                        placeholder="e.g. 28471920"
                        required
                        className="w-full bg-[#111111] border border-[#333333] px-3 py-2 text-xs text-[#ffffff] focus:border-[#22c55e] outline-none font-mono"
                      />
                    </div>
                    <div className="space-y-1">
                      <label className="text-xs font-medium text-[#cccccc] block">
                        Telegram API Hash *
                      </label>
                      <input
                        type="text"
                        value={byokApiHash}
                        onChange={(e) => setByokApiHash(e.target.value)}
                        placeholder="32-char hex string"
                        required
                        className="w-full bg-[#111111] border border-[#333333] px-3 py-2 text-xs text-[#ffffff] focus:border-[#22c55e] outline-none font-mono"
                      />
                    </div>
                  </div>

                  <Button
                    type="submit"
                    variant="primary"
                    size="md"
                    className="w-full justify-center text-xs font-medium"
                    disabled={isLoading}
                    icon={<QrCode size={14} />}
                  >
                    {isLoading ? 'Initializing Node...' : 'Generate Sovereign QR Code'}
                  </Button>
                </form>
              )}

              {/* Active QR Session Display (Waiting, Expired, Failed) */}
              {!editingByokCreds && qrUrl && (qrStatus === 'waiting' || qrStatus === 'expired' || qrStatus === 'failed') && (
                <div className="space-y-4">
                  {/* Active Credentials Bar */}
                  <div className="flex items-center justify-between p-2 bg-[#111111] border border-[#222222] text-[11px]">
                    <div className="flex items-center gap-2 font-mono text-[#888888]">
                      <span className="w-2 h-2 rounded-full bg-[#22c55e]" />
                      <span>Sovereign App: <strong className="text-[#ffffff]">API ID #{byokApiId}</strong></span>
                    </div>
                    <button
                      type="button"
                      onClick={() => setEditingByokCreds(true)}
                      className="text-[#888888] hover:text-[#ffffff] flex items-center gap-1 text-[11px]"
                    >
                      <Edit3 size={11} /> Edit Keys
                    </button>
                  </div>

                  <div className="flex flex-col sm:flex-row items-center gap-6">
                    {/* QR Box */}
                    <div className="w-52 h-52 bg-[#000000] border-2 border-[#333333] p-3 flex flex-col items-center justify-center relative flex-shrink-0">
                      {qrStatus === 'failed' ? (
                        <div className="w-full h-full bg-[#0d0d0d] p-3 flex flex-col items-center justify-center text-center space-y-2.5">
                          <div className="w-7 h-7 rounded-full bg-[#1f0a0a] border border-[#ef4444] flex items-center justify-center text-[#ef4444]">
                            <AlertTriangle size={14} />
                          </div>
                          <div>
                            <div className="text-[11px] font-bold text-[#ef4444] uppercase tracking-wider">Connection Failed</div>
                            <div className="text-[10px] text-[#777777] mt-0.5 line-clamp-2">{error || 'Could not start QR session'}</div>
                          </div>
                          <Button
                            variant="secondary"
                            size="sm"
                            onClick={() => handleStartByokQr()}
                            className="w-full text-[10px] py-1 flex items-center justify-center gap-1"
                          >
                            <RefreshCw size={11} /> Retry Connection
                          </Button>
                        </div>
                      ) : qrStatus === 'expired' || (qrStatus === 'waiting' && qrExpiresAt != null && Math.floor(Date.now() / 1000) >= qrExpiresAt) ? (
                        <div className="w-full h-full bg-[#0d0d0d] p-3 flex flex-col items-center justify-center text-center space-y-2.5">
                          <div className="w-7 h-7 rounded-full bg-[#1f0a0a] border border-[#ef4444] flex items-center justify-center text-[#ef4444]">
                            <AlertTriangle size={14} />
                          </div>
                          <div>
                            <div className="text-[11px] font-bold text-[#ef4444] uppercase tracking-wider">Session Expired</div>
                            <div className="text-[10px] text-[#777777] mt-0.5">5-minute window ended</div>
                          </div>
                          <Button
                            variant="secondary"
                            size="sm"
                            onClick={() => handleStartByokQr()}
                            className="w-full text-[10px] py-1 flex items-center justify-center gap-1"
                          >
                            <RefreshCw size={11} /> New QR Code
                          </Button>
                        </div>
                      ) : qrUrl ? (
                        <>
                          <QRCodeSVG
                            value={qrUrl}
                            size={180}
                            bgColor="#000000"
                            fgColor="#ffffff"
                            level="M"
                          />
                          <div className="absolute inset-0 border border-[#22c55e] opacity-30 pointer-events-none" />
                        </>
                      ) : (
                        <div className="text-xs text-[#555555] animate-pulse">Generating QR...</div>
                      )}
                    </div>

                    {/* Step Instructions */}
                    <div className="space-y-3 text-xs flex-1">
                      <div className="text-xs font-medium text-[#f0f0f0]">
                        Scan to Link Sovereign Device:
                      </div>
                      <ol className="space-y-2 text-[#cccccc] list-decimal list-inside text-xs leading-relaxed">
                        <li>Open <strong className="text-[#ffffff] font-medium">Telegram</strong> on your phone</li>
                        <li>Go to <strong className="text-[#ffffff] font-medium">Settings</strong> → <strong className="text-[#ffffff] font-medium">Devices</strong></li>
                        <li>Tap <strong className="text-[#22c55e] font-medium">Link Desktop Device</strong></li>
                        <li>Point your camera at this QR code</li>
                      </ol>

                      <div className="pt-2 flex items-center justify-between text-xs text-[#888888] border-t border-[#1c1c1c]">
                        <div className="flex items-center gap-1.5 font-mono text-[11px]">
                          <span className={`w-1.5 h-1.5 rounded-full ${timeLeft > 0 && qrStatus !== 'expired' && qrStatus !== 'failed' ? 'bg-[#22c55e] animate-pulse' : 'bg-[#ef4444]'}`} />
                          <span>
                            {qrStatus === 'failed' ? (
                              <strong className="text-[#ef4444] font-semibold">Connection Error</strong>
                            ) : (
                              <>Expires in: <strong className="text-[#ffffff] font-semibold">{formatTime(timeLeft)}</strong></>
                            )}
                          </span>
                        </div>
                        <button
                          onClick={() => handleStartByokQr()}
                          className="text-[#22c55e] hover:underline flex items-center gap-1 text-xs font-medium"
                        >
                          <RefreshCw size={11} /> {qrStatus === 'failed' ? 'Retry' : 'Refresh'}
                        </button>
                      </div>
                    </div>
                  </div>
                </div>
              )}

              {/* State: 2FA Password Required */}
              {qrStatus === '2fa_required' && (
                <form onSubmit={handle2FASubmit} className="space-y-4 py-2">
                  <div className="p-3 bg-[#161208] border border-[#78350f] text-xs text-[#f59e0b] flex items-center gap-2">
                    <Key size={16} />
                    <span>Your Telegram account has <strong>Two-Step Verification</strong> enabled.</span>
                  </div>
                  <div className="space-y-1.5">
                    <label className="text-[11px] uppercase text-[#888888] block">Cloud Password</label>
                    <input
                      type="password"
                      value={twoFactorPassword}
                      onChange={(e) => setTwoFactorPassword(e.target.value)}
                      placeholder="Enter your 2FA password"
                      autoFocus
                      className="w-full bg-[#111111] border border-[#333333] px-3 py-2 text-xs text-[#ffffff] focus:border-[#ffffff] outline-none"
                    />
                  </div>
                  <Button type="submit" variant="primary" size="md" className="w-full" disabled={isLoading}>
                    {isLoading ? 'Verifying...' : 'Verify Cloud Password'}
                  </Button>
                </form>
              )}

              {/* State: Scan Confirmed -> Channel Allocation */}
              {qrStatus === 'confirmed' && (
                isLoading ? (
                  <div className="py-10 text-center space-y-4">
                    <div className="w-8 h-8 border-2 border-[#22c55e] border-t-transparent animate-spin mx-auto" />
                    <div>
                      <div className="text-xs font-bold text-[#22c55e] uppercase tracking-wider">
                        Telegram Confirmed: Welcome, {scannedUser?.first_name || 'User'}!
                      </div>
                      <div className="text-[11px] text-[#777777] mt-1">
                        Attaching storage vault and initializing Sovereign Node...
                      </div>
                    </div>
                  </div>
                ) : scannedUser ? (
                  <div className="space-y-5 py-1">
                    <div className="p-3 bg-[#0d1f12] border border-[#166534] text-xs text-[#22c55e] flex items-center gap-2">
                      <CheckCircle2 size={16} />
                      <span>Telegram Verified: Welcome, <strong>{scannedUser.first_name || 'User'}</strong>!</span>
                    </div>

                    <div className="space-y-1.5">
                      <label className="text-[11px] uppercase text-[#888888] block">
                        Account Display Name
                      </label>
                      <input
                        type="text"
                        value={displayName}
                        onChange={(e) => setDisplayName(e.target.value)}
                        placeholder="e.g. Ayush"
                        className="w-full bg-[#111111] border border-[#333333] px-3 py-2 text-xs text-[#ffffff] focus:border-[#ffffff] outline-none"
                      />
                      <span className="text-xs text-[#888888]">
                        Automatically detected from your Telegram profile. You can keep or edit it.
                      </span>
                    </div>

                    <div className="space-y-2 pt-2 border-t border-[#1c1c1c]">
                      <label className="text-xs font-medium text-[#cccccc] block">
                        Storage Channel Allocation
                      </label>

                      {availableChannels.length > 0 && (
                        <label className="flex items-start gap-2.5 p-2.5 bg-[#111111] border border-[#222222] cursor-pointer hover:border-[#383838]">
                          <input
                            type="radio"
                            name="channel_action_byok"
                            checked={selectedChannelAction === 'select_existing'}
                            onChange={() => setSelectedChannelAction('select_existing')}
                            className="mt-0.5 accent-[#ffffff]"
                          />
                          <div className="text-xs flex-1">
                            <span className="font-semibold text-[#ffffff] block">Connect to Existing Storage Channel</span>
                            <select
                              value={selectedChannelId}
                              onChange={(e) => {
                                setSelectedChannelId(e.target.value)
                                setSelectedChannelAction('select_existing')
                              }}
                              className="w-full bg-[#080808] border border-[#333333] mt-2 px-2 py-1.5 text-xs text-[#e5e5e5] outline-none"
                            >
                              {availableChannels.map((c) => (
                                <option key={c.id} value={c.id}>
                                  {c.title} ({c.id})
                                </option>
                              ))}
                            </select>
                          </div>
                        </label>
                      )}

                      <label className="flex items-start gap-2.5 p-2.5 bg-[#111111] border border-[#222222] cursor-pointer hover:border-[#383838]">
                        <input
                          type="radio"
                          name="channel_action_byok"
                          checked={selectedChannelAction === 'auto_create'}
                          onChange={() => setSelectedChannelAction('auto_create')}
                          className="mt-0.5 accent-[#ffffff]"
                        />
                        <div className="text-xs">
                          <span className="font-semibold text-[#ffffff] block">
                            Auto-Create New Private Storage Channel (Sign-Up)
                          </span>
                          <span className="text-xs text-[#888888] block mt-0.5">
                            Creates a private channel named "{displayName || 'User'}'s Vayom Vault" automatically.
                          </span>
                        </div>
                      </label>
                    </div>

                    <Button
                      onClick={handleQrFinalize}
                      variant="primary"
                      size="md"
                      className="w-full justify-center text-xs font-medium"
                      disabled={isLoading}
                      icon={<ArrowRight size={14} />}
                    >
                      {isLoading ? 'Allocating Node...' : 'Enter Vayom Vault'}
                    </Button>
                  </div>
                ) : null
              )}
            </div>
          )}

          {/* TAB 2: BOT API TOKEN & CHANNEL WIZARD (PRIMARY / ISOLATED) */}
          {activeTab === 'bot' && (
            <form onSubmit={handleBotSubmit} className="p-6 space-y-5">
              <div className="flex items-center justify-between pb-3 border-b border-[#1c1c1c]">
                <div>
                  <h2 className="text-sm font-semibold tracking-normal text-[#ffffff]">
                    Dedicated Storage Bot & Channel
                  </h2>
                  <p className="text-xs text-[#888888] mt-0.5">
                    Sandboxed bot mode • Zero personal chat access • 2 GB chunk limits
                  </p>
                </div>
                <Badge variant="success">PRIMARY // ISOLATED</Badge>
              </div>

              {/* Step-by-Step Interactive Guide */}
              <div className="p-3.5 bg-[#0f0f0f] border border-[#222222] space-y-2.5 text-xs text-[#bbbbbb]">
                <div className="font-medium text-[#ffffff] text-xs flex items-center justify-between">
                  <span>How to create your storage bot:</span>
                  <a
                    href="https://t.me/BotFather"
                    target="_blank"
                    rel="noreferrer"
                    className="text-[#22c55e] hover:underline flex items-center gap-1 text-xs"
                  >
                    Open @BotFather <ExternalLink size={11} />
                  </a>
                </div>
                <ol className="space-y-1.5 list-decimal list-inside text-xs text-[#999999] leading-relaxed">
                  <li>Message <strong className="text-[#ffffff]">@BotFather</strong> on Telegram and send <code className="bg-[#1c1c1c] text-[#ffffff] px-1 py-0.5 font-mono">/newbot</code></li>
                  <li>Choose a bot name and username (e.g. <code className="text-[#e5e5e5] font-mono">MyVaultStorageBot</code>)</li>
                  <li>Copy the HTTP API Token provided by BotFather</li>
                  <li>Create a new <strong className="text-[#ffffff]">Private Channel</strong> and add your bot as an <strong className="text-[#ffffff]">Administrator</strong></li>
                </ol>
              </div>

              {/* Inputs */}
              <div className="space-y-3 text-xs">
                <div className="space-y-1">
                  <label className="text-xs font-medium text-[#cccccc] block">Telegram Bot Token *</label>
                  <input
                    type="text"
                    value={botToken}
                    onChange={(e) => setBotToken(e.target.value)}
                    placeholder="e.g. 7123456789:AAFx..."
                    required
                    className="w-full bg-[#111111] border border-[#333333] px-3 py-2 text-xs text-[#ffffff] focus:border-[#ffffff] outline-none font-mono"
                  />
                </div>

                <div className="space-y-1">
                  <label className="text-xs font-medium text-[#cccccc] block">Storage Channel ID *</label>
                  <input
                    type="text"
                    value={botChannelId}
                    onChange={(e) => setBotChannelId(e.target.value)}
                    placeholder="e.g. -1002345678901"
                    required
                    className="w-full bg-[#111111] border border-[#333333] px-3 py-2 text-xs text-[#ffffff] focus:border-[#ffffff] outline-none font-mono"
                  />
                </div>

                <div className="space-y-1">
                  <label className="text-xs font-medium text-[#cccccc] block">Account Display Name (Optional)</label>
                  <input
                    type="text"
                    value={botDisplayName}
                    onChange={(e) => setBotDisplayName(e.target.value)}
                    placeholder="e.g. Storage Bot Node"
                    className="w-full bg-[#111111] border border-[#333333] px-3 py-2 text-xs text-[#ffffff] focus:border-[#ffffff] outline-none"
                  />
                </div>
              </div>

              <Button
                type="submit"
                variant="primary"
                size="md"
                className="w-full justify-center text-xs font-medium"
                disabled={isLoading}
              >
                {isLoading ? 'Connecting...' : 'Connect Storage Bot'}
              </Button>
            </form>
          )}

          {/* TAB 3: QUICK QR SCAN (SECONDARY / SHARED GATEWAY) */}
          {activeTab === 'qr' && (
            <div className="p-6 space-y-6">
              <div className="flex items-center justify-between pb-3 border-b border-[#1c1c1c]">
                <div>
                  <h2 className="text-sm font-semibold tracking-normal text-[#ffffff] flex items-center gap-2">
                    Quick QR Scan (Shared Gateway)
                  </h2>
                  <p className="text-xs text-[#888888] mt-0.5">
                    1-Click MTProto evaluation • Shared node infrastructure
                  </p>
                </div>
                <Badge variant="warning">SECONDARY // SHARED NODE</Badge>
              </div>

              {/* Advisory Warning Banner */}
              <div className="p-3 bg-[#171408] border border-[#78350f] text-xs text-[#f59e0b] flex items-start gap-2.5">
                <AlertTriangle size={16} className="text-[#f59e0b] flex-shrink-0 mt-0.5" />
                <div className="space-y-1">
                  <strong className="block text-[#fbbf24] font-medium">Shared Gateway Advisory Notice</strong>
                  <p className="text-[11px] text-[#fde68a] leading-relaxed">
                    This mode uses shared application credentials. High-volume transfers or bulk file storage may trigger Telegram's rate-limits or temporary restrictions on shared apps. For long-term production and zero risk of ban, we recommend switching to{' '}
                    <button
                      type="button"
                      onClick={() => setActiveTab('byok_qr')}
                      className="underline font-bold text-[#ffffff] hover:text-[#22c55e]"
                    >
                      BYOK + QR
                    </button>{' '}
                    or{' '}
                    <button
                      type="button"
                      onClick={() => setActiveTab('bot')}
                      className="underline font-bold text-[#ffffff] hover:text-[#22c55e]"
                    >
                      Bot & Channel
                    </button>.
                  </p>
                </div>
              </div>

              {/* State: Awaiting Scan, Expired, or Failed */}
              {(qrStatus === 'waiting' || qrStatus === 'idle' || qrStatus === 'expired' || qrStatus === 'failed') && (
                <div className="flex flex-col sm:flex-row items-center gap-6">
                  {/* QR Box */}
                  <div className="w-52 h-52 bg-[#000000] border-2 border-[#333333] p-3 flex flex-col items-center justify-center relative flex-shrink-0">
                    {qrStatus === 'failed' ? (
                      <div className="w-full h-full bg-[#0d0d0d] p-3 flex flex-col items-center justify-center text-center space-y-2.5">
                        <div className="w-7 h-7 rounded-full bg-[#1f0a0a] border border-[#ef4444] flex items-center justify-center text-[#ef4444]">
                          <AlertTriangle size={14} />
                        </div>
                        <div>
                          <div className="text-[11px] font-bold text-[#ef4444] uppercase tracking-wider">Connection Failed</div>
                          <div className="text-[10px] text-[#777777] mt-0.5 line-clamp-2">{error || 'Could not start QR session'}</div>
                        </div>
                        <Button
                          variant="secondary"
                          size="sm"
                          onClick={() => {
                            hasAttemptedRef.current = true
                            startQrSession()
                          }}
                          className="w-full text-[10px] py-1 flex items-center justify-center gap-1"
                        >
                          <RefreshCw size={11} /> Retry Connection
                        </Button>
                      </div>
                    ) : qrStatus === 'expired' || (qrStatus === 'waiting' && qrExpiresAt != null && Math.floor(Date.now() / 1000) >= qrExpiresAt) ? (
                      <div className="w-full h-full bg-[#0d0d0d] p-3 flex flex-col items-center justify-center text-center space-y-2.5">
                        <div className="w-7 h-7 rounded-full bg-[#1f0a0a] border border-[#ef4444] flex items-center justify-center text-[#ef4444]">
                          <AlertTriangle size={14} />
                        </div>
                        <div>
                          <div className="text-[11px] font-bold text-[#ef4444] uppercase tracking-wider">Session Expired</div>
                          <div className="text-[10px] text-[#777777] mt-0.5">5-minute window ended</div>
                        </div>
                        <Button
                          variant="secondary"
                          size="sm"
                          onClick={() => {
                            hasAttemptedRef.current = true
                            startQrSession()
                          }}
                          className="w-full text-[10px] py-1 flex items-center justify-center gap-1"
                        >
                          <RefreshCw size={11} /> New QR Code
                        </Button>
                      </div>
                    ) : qrUrl ? (
                      <>
                        <QRCodeSVG
                          value={qrUrl}
                          size={180}
                          bgColor="#000000"
                          fgColor="#ffffff"
                          level="M"
                        />
                        <div className="absolute inset-0 border border-[#f59e0b] opacity-30 pointer-events-none" />
                      </>
                    ) : (
                      <div className="text-xs text-[#555555] animate-pulse">Generating QR...</div>
                    )}
                  </div>

                  {/* Step Instructions */}
                  <div className="space-y-3 text-xs flex-1">
                    <div className="text-xs font-medium text-[#f0f0f0]">
                      How to connect via Shared Gateway:
                    </div>
                    <ol className="space-y-2 text-[#cccccc] list-decimal list-inside text-xs leading-relaxed">
                      <li>Open <strong className="text-[#ffffff] font-medium">Telegram</strong> on your phone</li>
                      <li>Go to <strong className="text-[#ffffff] font-medium">Settings</strong> → <strong className="text-[#ffffff] font-medium">Devices</strong></li>
                      <li>Tap <strong className="text-[#f59e0b] font-medium">Link Desktop Device</strong></li>
                      <li>Point your phone camera at this QR code</li>
                    </ol>

                    <div className="pt-2 flex items-center justify-between text-xs text-[#888888] border-t border-[#1c1c1c]">
                      <div className="flex items-center gap-1.5 font-mono text-[11px]">
                        <span className={`w-1.5 h-1.5 rounded-full ${timeLeft > 0 && qrStatus !== 'expired' && qrStatus !== 'failed' ? 'bg-[#f59e0b] animate-pulse' : 'bg-[#ef4444]'}`} />
                        <span>
                          {qrStatus === 'failed' ? (
                            <strong className="text-[#ef4444] font-semibold">Connection Error</strong>
                          ) : (
                            <>Expires in: <strong className="text-[#ffffff] font-semibold">{formatTime(timeLeft)}</strong></>
                          )}
                        </span>
                      </div>
                      <button
                        onClick={() => {
                          hasAttemptedRef.current = true
                          startQrSession()
                        }}
                        className="text-[#f59e0b] hover:underline flex items-center gap-1 text-xs font-medium"
                      >
                        <RefreshCw size={11} /> {qrStatus === 'failed' ? 'Retry' : 'Refresh'}
                      </button>
                    </div>
                  </div>
                </div>
              )}

              {/* State: 2FA Password Required */}
              {qrStatus === '2fa_required' && (
                <form onSubmit={handle2FASubmit} className="space-y-4 py-2">
                  <div className="p-3 bg-[#161208] border border-[#78350f] text-xs text-[#f59e0b] flex items-center gap-2">
                    <Key size={16} />
                    <span>Your Telegram account has <strong>Two-Step Verification</strong> enabled.</span>
                  </div>
                  <div className="space-y-1.5">
                    <label className="text-[11px] uppercase text-[#888888] block">Cloud Password</label>
                    <input
                      type="password"
                      value={twoFactorPassword}
                      onChange={(e) => setTwoFactorPassword(e.target.value)}
                      placeholder="Enter your 2FA password"
                      autoFocus
                      className="w-full bg-[#111111] border border-[#333333] px-3 py-2 text-xs text-[#ffffff] focus:border-[#ffffff] outline-none"
                    />
                  </div>
                  <Button type="submit" variant="primary" size="md" className="w-full" disabled={isLoading}>
                    {isLoading ? 'Verifying...' : 'Verify Cloud Password'}
                  </Button>
                </form>
              )}

              {/* State: Scan Confirmed -> Auto-connecting or Account Setup */}
              {qrStatus === 'confirmed' && (
                isLoading ? (
                  <div className="py-10 text-center space-y-4">
                    <div className="w-8 h-8 border-2 border-[#f59e0b] border-t-transparent animate-spin mx-auto" />
                    <div>
                      <div className="text-xs font-bold text-[#f59e0b] uppercase tracking-wider">
                        Telegram Confirmed: Welcome, {scannedUser?.first_name || 'User'}!
                      </div>
                      <div className="text-[11px] text-[#777777] mt-1">
                        Attaching storage vault and loading Drive...
                      </div>
                    </div>
                  </div>
                ) : scannedUser ? (
                  <div className="space-y-5 py-1">
                    <div className="p-3 bg-[#0d1f12] border border-[#166534] text-xs text-[#22c55e] flex items-center gap-2">
                      <CheckCircle2 size={16} />
                      <span>Telegram Verified: Welcome, <strong>{scannedUser.first_name || 'User'}</strong>!</span>
                    </div>

                    <div className="space-y-1.5">
                      <label className="text-[11px] uppercase text-[#888888] block">
                        Account Display Name
                      </label>
                      <input
                        type="text"
                        value={displayName}
                        onChange={(e) => setDisplayName(e.target.value)}
                        placeholder="e.g. Ayush"
                        className="w-full bg-[#111111] border border-[#333333] px-3 py-2 text-xs text-[#ffffff] focus:border-[#ffffff] outline-none"
                      />
                      <span className="text-xs text-[#888888]">
                        Automatically detected from your Telegram profile. You can keep or edit it.
                      </span>
                    </div>

                    <div className="space-y-2 pt-2 border-t border-[#1c1c1c]">
                      <label className="text-xs font-medium text-[#cccccc] block">
                        Storage Channel Allocation
                      </label>

                      {availableChannels.length > 0 && (
                        <label className="flex items-start gap-2.5 p-2.5 bg-[#111111] border border-[#222222] cursor-pointer hover:border-[#383838]">
                          <input
                            type="radio"
                            name="channel_action_shared"
                            checked={selectedChannelAction === 'select_existing'}
                            onChange={() => setSelectedChannelAction('select_existing')}
                            className="mt-0.5 accent-[#ffffff]"
                          />
                          <div className="text-xs flex-1">
                            <span className="font-semibold text-[#ffffff] block">Connect to Existing Storage Channel</span>
                            <select
                              value={selectedChannelId}
                              onChange={(e) => {
                                setSelectedChannelId(e.target.value)
                                setSelectedChannelAction('select_existing')
                              }}
                              className="w-full bg-[#080808] border border-[#333333] mt-2 px-2 py-1.5 text-xs text-[#e5e5e5] outline-none"
                            >
                              {availableChannels.map((c) => (
                                <option key={c.id} value={c.id}>
                                  {c.title} ({c.id})
                                </option>
                              ))}
                            </select>
                          </div>
                        </label>
                      )}

                      <label className="flex items-start gap-2.5 p-2.5 bg-[#111111] border border-[#222222] cursor-pointer hover:border-[#383838]">
                        <input
                          type="radio"
                          name="channel_action_shared"
                          checked={selectedChannelAction === 'auto_create'}
                          onChange={() => setSelectedChannelAction('auto_create')}
                          className="mt-0.5 accent-[#ffffff]"
                        />
                        <div className="text-xs">
                          <span className="font-semibold text-[#ffffff] block">
                            Auto-Create New Private Storage Channel (Sign-Up)
                          </span>
                          <span className="text-xs text-[#888888] block mt-0.5">
                            Creates a private channel named "{displayName || 'User'}'s Vayom Vault" automatically.
                          </span>
                        </div>
                      </label>
                    </div>

                    <Button
                      onClick={handleQrFinalize}
                      variant="primary"
                      size="md"
                      className="w-full justify-center text-xs font-medium"
                      disabled={isLoading}
                      icon={<ArrowRight size={14} />}
                    >
                      {isLoading ? 'Allocating Node...' : 'Enter Vayom Vault'}
                    </Button>
                  </div>
                ) : null
              )}
            </div>
          )}

          {/* TAB 4: SOVEREIGN DEVELOPER MODE */}
          {activeTab === 'developer' && (
            <form onSubmit={handleDeveloperSubmit} className="p-6 space-y-5">
              <div className="pb-3 border-b border-[#1c1c1c]">
                <h2 className="text-sm font-semibold tracking-normal text-[#ffffff]">
                  Sovereign Mode (Developer Direct Node)
                </h2>
                <p className="text-xs text-[#888888] mt-0.5">
                  Custom API_ID & API_HASH • Complete architectural sovereignty
                </p>
              </div>

              {/* Developer Documentation Box */}
              <div className="p-3.5 bg-[#0f0f0f] border border-[#222222] space-y-2 text-xs text-[#bbbbbb]">
                <div className="font-medium text-[#ffffff] text-xs flex items-center justify-between">
                  <span>How to get Telegram API Credentials:</span>
                  <a
                    href="https://my.telegram.org"
                    target="_blank"
                    rel="noreferrer"
                    className="text-[#22c55e] hover:underline flex items-center gap-1 text-xs"
                  >
                    my.telegram.org <ExternalLink size={11} />
                  </a>
                </div>
                <ol className="space-y-1 list-decimal list-inside text-xs text-[#999999] leading-relaxed">
                  <li>Log in to <strong className="text-[#ffffff]">my.telegram.org</strong> with your phone number</li>
                  <li>Click <strong className="text-[#ffffff]">API development tools</strong></li>
                  <li>Fill in App title (e.g. <code className="text-[#e5e5e5] font-mono">VayomVault</code>) and submit</li>
                  <li>Copy your numeric <code className="text-[#ffffff] font-mono">api_id</code> and 32-char <code className="text-[#ffffff] font-mono">api_hash</code></li>
                </ol>
              </div>

              <div className="space-y-3 text-xs">
                <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
                  <div className="space-y-1">
                    <label className="text-xs font-medium text-[#cccccc] block">API ID (Numeric) *</label>
                    <input
                      type="number"
                      value={devApiId}
                      onChange={(e) => setDevApiId(e.target.value)}
                      placeholder="e.g. 28471920"
                      required
                      className="w-full bg-[#111111] border border-[#333333] px-3 py-2 text-xs text-[#ffffff] focus:border-[#ffffff] outline-none font-mono"
                    />
                  </div>
                  <div className="space-y-1">
                    <label className="text-xs font-medium text-[#cccccc] block">API Hash *</label>
                    <input
                      type="text"
                      value={devApiHash}
                      onChange={(e) => setDevApiHash(e.target.value)}
                      placeholder="32-char hex string"
                      required
                      className="w-full bg-[#111111] border border-[#333333] px-3 py-2 text-xs text-[#ffffff] focus:border-[#ffffff] outline-none font-mono"
                    />
                  </div>
                </div>

                <div className="space-y-1">
                  <label className="text-xs font-medium text-[#cccccc] block">Bot Token *</label>
                  <input
                    type="text"
                    value={devBotToken}
                    onChange={(e) => setDevBotToken(e.target.value)}
                    placeholder="HTTP Bot Token from @BotFather"
                    required
                    className="w-full bg-[#111111] border border-[#333333] px-3 py-2 text-xs text-[#ffffff] focus:border-[#ffffff] outline-none font-mono"
                  />
                </div>

                <div className="space-y-1">
                  <label className="text-xs font-medium text-[#cccccc] block">Storage Channel ID *</label>
                  <input
                    type="text"
                    value={devChannelId}
                    onChange={(e) => setDevChannelId(e.target.value)}
                    placeholder="e.g. -1002345678901"
                    required
                    className="w-full bg-[#111111] border border-[#333333] px-3 py-2 text-xs text-[#ffffff] focus:border-[#ffffff] outline-none font-mono"
                  />
                </div>
              </div>

              <Button
                type="submit"
                variant="primary"
                size="md"
                className="w-full justify-center text-xs font-medium"
                disabled={isLoading}
              >
                {isLoading ? 'Verifying...' : 'Initialize Sovereign Node'}
              </Button>
            </form>
          )}
        </div>
      </main>

      {/* Footer */}
      <footer className="h-10 border-t border-[#1c1c1c] px-6 flex items-center justify-between text-[11px] text-[#555555] bg-[#050505]">
        <span>VAYOM DRIVE v1.0.0 // ZERO-KNOWLEDGE UTILITY</span>
        <span>STANDALONE CLIENT</span>
      </footer>
    </div>
  )
}
