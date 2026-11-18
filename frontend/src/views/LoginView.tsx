import React, { useState, useEffect } from 'react'
import { QRCodeSVG } from 'qrcode.react'
import { useAuthStore } from '../store/useAuthStore'
import {
  ArrowLeft,
  ExternalLink,
  RefreshCw,
  HardDrive,
  Send,
  AlertCircle,
  CheckCircle2,
} from 'lucide-react'

export const LoginView: React.FC = () => {
  const {
    authStep,
    setAuthStep,
    qrMode,
    setQrMode,
    hasSavedKeys,
    savedApiId,
    devModeEnabled,
    qrUrl,
    qrStatus,
    scannedUser,
    availableChannels,
    defaultChannelId,
    startQrSession,
    saveManualKeys,
    requestByokCode,
    extractByokKeys,
    submit2FA,
    finalizeQr,
    testNasConnection,
    saveNasConfig,
    loginWithBot,
    loginWithDeveloper,
    error,
    clearError,
    isLoading,
    clearSensitiveFormState,
  } = useAuthStore()

  // Clean sensitive state on unmount
  useEffect(() => {
    return () => {
      clearSensitiveFormState()
    }
  }, [clearSensitiveFormState])

  // Screen-specific local form state
  const [phone, setPhone] = useState('')
  const [randomHash, setRandomHash] = useState('')
  const [code, setCode] = useState('')
  const [showManualFields, setShowManualFields] = useState(false)
  const [manualApiId, setManualApiId] = useState('')
  const [manualApiHash, setManualApiHash] = useState('')
  const [twoFactorPassword, setTwoFactorPassword] = useState('')

  // NAS local form state
  const [nasPath, setNasPath] = useState('./data/nas_vault')
  const [nasDisplayName, setNasDisplayName] = useState('My NAS Vault')
  const [nasTestResult, setNasTestResult] = useState<{
    tested: boolean
    success: boolean
    message?: string
    error?: string
  } | null>(null)

  // Bot & Developer local form state
  const [botToken, setBotToken] = useState('')
  const [botChannelId, setBotChannelId] = useState('')
  const [botDisplayName, setBotDisplayName] = useState('')
  const [devApiId, setDevApiId] = useState('')
  const [devApiHash, setDevApiHash] = useState('')
  const [devBotToken, setDevBotToken] = useState('')
  const [devChannelId, setDevChannelId] = useState('')
  const [devDisplayName, setDevDisplayName] = useState('')

  // Finalize channel selection
  const [displayName, setDisplayName] = useState('')
  const [selectedChannelAction, setSelectedChannelAction] = useState<'auto_create' | 'select_existing'>('auto_create')
  const [selectedChannelId, setSelectedChannelId] = useState('')

  useEffect(() => {
    if (scannedUser) {
      setDisplayName(scannedUser.first_name || scannedUser.display_name || 'User')
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

  // Handlers
  const handleStartTelegram = () => {
    clearError()
    if (hasSavedKeys) {
      setQrMode('own')
      startQrSession({ mode: 'own' })
    } else {
      setAuthStep('needs_keys')
    }
  }

  const handleSkipSharedKeys = () => {
    clearError()
    setQrMode('shared')
    startQrSession({ mode: 'shared' })
  }

  const handleManualKeysSubmit = async (e: React.FormEvent) => {
    e.preventDefault()
    const numId = Number(manualApiId.trim())
    const hash = manualApiHash.trim()
    if (!numId || isNaN(numId) || numId <= 0) {
      useAuthStore.setState({ error: 'Please enter a valid numeric API ID.' })
      return
    }
    if (hash.length !== 32) {
      useAuthStore.setState({ error: 'Please enter a valid 32-character API Hash.' })
      return
    }

    try {
      clearError()
      await saveManualKeys(numId, hash)
      setQrMode('own')
      await startQrSession({ mode: 'own', apiId: numId, apiHash: hash })
    } catch {
      // Error is set in store
    }
  }

  const handleSendPhone = async (e: React.FormEvent) => {
    e.preventDefault()
    if (!phone.trim()) return
    try {
      clearError()
      const hash = await requestByokCode(phone.trim())
      setRandomHash(hash)
      setAuthStep('auto_code')
    } catch {
      // Error is set in store
    }
  }

  const handleConfirmCode = async (e: React.FormEvent) => {
    e.preventDefault()
    if (!code.trim() || !randomHash) return
    try {
      clearError()
      const creds = await extractByokKeys(phone.trim(), randomHash, code.trim())
      setCode('') // Discard code immediately
      setQrMode('own')
      await startQrSession({ mode: 'own', apiId: creds.apiId, apiHash: creds.apiHash })
    } catch {
      // Error is set in store
    }
  }

  const handle2FASubmit = async (e: React.FormEvent) => {
    e.preventDefault()
    if (!twoFactorPassword) return
    try {
      clearError()
      await submit2FA(twoFactorPassword)
      setTwoFactorPassword('')
    } catch {
      // Error is set in store
    }
  }

  const handleTestNas = async () => {
    clearError()
    setNasTestResult(null)
    const result = await testNasConnection(nasPath)
    if (result.success) {
      const freeGb = result.free_bytes ? (result.free_bytes / (1024 * 1024 * 1024)).toFixed(1) : '0'
      setNasTestResult({
        tested: true,
        success: true,
        message: `Connection successful. ${freeGb} GB free space available.`,
      })
    } else {
      setNasTestResult({
        tested: true,
        success: false,
        error: result.error || 'Connection check failed. Verify path and directory permissions.',
      })
    }
  }

  const handleSaveNas = async (e: React.FormEvent) => {
    e.preventDefault()
    try {
      clearError()
      await saveNasConfig(nasPath, nasDisplayName)
    } catch {
      // Error is set in store
    }
  }

  const handleBotSubmit = async (e: React.FormEvent) => {
    e.preventDefault()
    if (!botToken || !botChannelId) return
    try {
      clearError()
      await loginWithBot(botToken, botChannelId, botDisplayName)
    } catch {
      // Error is set in store
    }
  }

  const handleDevSubmit = async (e: React.FormEvent) => {
    e.preventDefault()
    if (!devApiId || !devApiHash || !devBotToken || !devChannelId) return
    try {
      clearError()
      await loginWithDeveloper(Number(devApiId), devApiHash, devBotToken, devChannelId, devDisplayName)
    } catch {
      // Error is set in store
    }
  }

  const handleFinalize = async () => {
    try {
      clearError()
      await finalizeQr(
        displayName.trim() || 'User',
        selectedChannelAction,
        selectedChannelAction === 'select_existing' ? selectedChannelId : undefined
      )
    } catch {
      // Error is set in store
    }
  }

  return (
    <div className="min-h-screen bg-[#14171C] text-[#E8EAEE] flex flex-col justify-between select-none">
      {/* Top Header */}
      <header className="h-14 px-6 flex items-center justify-between border-b border-[#2A303A]">
        <div className="flex items-center gap-2.5">
          <div className="w-5 h-5 bg-[#E8EAEE] flex items-center justify-center">
            <div className="w-2.5 h-2.5 bg-[#14171C]" />
          </div>
          <span className="text-sm font-semibold tracking-normal text-[#E8EAEE]">Vayom Drive</span>
        </div>
      </header>

      {/* Main Single-Column Card */}
      <main className="flex-1 flex items-center justify-center p-4 sm:p-6">
        <div className="w-full max-w-lg bg-[#1B1F26] p-6 sm:p-8 space-y-6">
          {/* Global Error Banner */}
          {error && (
            <div className="p-3 bg-[#241a1a] border border-[#E5534B] text-sm text-[#E5534B] flex items-start justify-between gap-3">
              <div className="flex items-start gap-2">
                <AlertCircle size={16} className="flex-shrink-0 mt-0.5" />
                <span>{error}</span>
              </div>
              <button
                onClick={clearError}
                className="text-[#8B93A1] hover:text-[#E8EAEE] text-sm leading-none"
                aria-label="Dismiss error"
              >
                ✕
              </button>
            </div>
          )}

          {/* SCREEN 1: CHOOSING */}
          {authStep === 'choosing' && (
            <div className="space-y-6">
              <div className="space-y-2">
                <h1 className="text-xl font-semibold text-[#E8EAEE]">Connect your storage</h1>
                <p className="text-sm text-[#8B93A1]">
                  Choose how you want to connect your cloud vault or local storage.
                </p>
              </div>

              <div className="space-y-3 pt-2">
                <button
                  type="button"
                  onClick={handleStartTelegram}
                  className="w-full py-2.5 px-4 bg-[#4C8DFF] text-[#ffffff] hover:bg-[#3b79e8] focus:ring-2 focus:ring-[#4C8DFF] text-sm font-medium transition-colors duration-150 flex items-center justify-center gap-2"
                >
                  <Send size={15} />
                  <span>Sign in with Telegram</span>
                </button>

                <button
                  type="button"
                  onClick={() => {
                    clearError()
                    setAuthStep('nas_form')
                  }}
                  className="w-full py-2.5 px-4 bg-[#242933] text-[#E8EAEE] hover:bg-[#2c3340] text-sm font-medium transition-colors duration-150 flex items-center justify-center gap-2"
                >
                  <HardDrive size={15} />
                  <span>Connect a NAS</span>
                </button>
              </div>

              <div className="pt-2 text-center">
                <button
                  type="button"
                  onClick={() => {
                    clearError()
                    setAuthStep('bot_form')
                  }}
                  className="text-sm text-[#4C8DFF] hover:underline"
                >
                  Other ways to sign in
                </button>
              </div>
            </div>
          )}

          {/* SCREEN 2: NEEDS KEYS */}
          {authStep === 'needs_keys' && (
            <div className="space-y-6">
              <div className="space-y-2">
                <h1 className="text-xl font-semibold text-[#E8EAEE]">Add your Telegram API keys</h1>
                <p className="text-sm text-[#8B93A1] leading-relaxed">
                  Telegram needs an API ID and hash for this app. Using your own keeps your traffic separate from other users.
                </p>
              </div>

              {!showManualFields ? (
                <div className="space-y-3 pt-2">
                  <button
                    type="button"
                    onClick={() => {
                      clearError()
                      setAuthStep('auto_phone')
                    }}
                    className="w-full py-2.5 px-4 bg-[#4C8DFF] text-[#ffffff] hover:bg-[#3b79e8] text-sm font-medium transition-colors duration-150"
                  >
                    Get keys automatically
                  </button>

                  <button
                    type="button"
                    onClick={() => setShowManualFields(true)}
                    className="w-full py-2.5 px-4 bg-[#242933] text-[#E8EAEE] hover:bg-[#2c3340] text-sm font-medium transition-colors duration-150"
                  >
                    I already have keys
                  </button>
                </div>
              ) : (
                <form onSubmit={handleManualKeysSubmit} className="space-y-4 pt-2">
                  <div className="space-y-1">
                    <label className="text-xs text-[#8B93A1] block">API ID</label>
                    <input
                      type="number"
                      value={manualApiId}
                      onChange={(e) => setManualApiId(e.target.value)}
                      placeholder="e.g. 28471920"
                      required
                      autoFocus
                      className="w-full bg-[#14171C] border border-[#2A303A] text-[#E8EAEE] focus:border-[#4C8DFF] focus:outline-none px-3.5 py-2 text-sm font-mono"
                    />
                  </div>

                  <div className="space-y-1">
                    <label className="text-xs text-[#8B93A1] block">API Hash</label>
                    <input
                      type="text"
                      value={manualApiHash}
                      onChange={(e) => setManualApiHash(e.target.value)}
                      placeholder="32-character hexadecimal hash"
                      required
                      className="w-full bg-[#14171C] border border-[#2A303A] text-[#E8EAEE] focus:border-[#4C8DFF] focus:outline-none px-3.5 py-2 text-sm font-mono"
                    />
                  </div>

                  <div className="flex gap-2.5 pt-1">
                    <button
                      type="button"
                      onClick={() => setShowManualFields(false)}
                      className="px-4 py-2.5 bg-[#242933] text-[#E8EAEE] hover:bg-[#2c3340] text-sm font-medium transition-colors duration-150"
                    >
                      Back
                    </button>
                    <button
                      type="submit"
                      disabled={isLoading}
                      className="flex-1 py-2.5 px-4 bg-[#4C8DFF] text-[#ffffff] hover:bg-[#3b79e8] text-sm font-medium transition-colors duration-150 disabled:opacity-50"
                    >
                      {isLoading ? 'Connecting...' : 'Continue'}
                    </button>
                  </div>
                </form>
              )}

              <div className="pt-2 flex items-center justify-between text-sm">
                <button
                  type="button"
                  onClick={() => {
                    clearError()
                    setAuthStep('choosing')
                  }}
                  className="text-[#8B93A1] hover:text-[#E8EAEE]"
                >
                  Back
                </button>
                <button
                  type="button"
                  onClick={handleSkipSharedKeys}
                  className="text-[#4C8DFF] hover:underline"
                >
                  Skip and use shared keys
                </button>
              </div>
            </div>
          )}

          {/* SCREEN 3: AUTO GENERATE - PHONE STEP */}
          {authStep === 'auto_phone' && (
            <form onSubmit={handleSendPhone} className="space-y-6">
              <div className="space-y-2">
                <h1 className="text-xl font-semibold text-[#E8EAEE]">Enter your phone number</h1>
                <p className="text-sm text-[#8B93A1] leading-relaxed">
                  Enter your Telegram phone number with international country code.
                </p>
              </div>

              <div className="space-y-1">
                <label className="text-xs text-[#8B93A1] block">Phone number</label>
                <input
                  type="tel"
                  value={phone}
                  onChange={(e) => setPhone(e.target.value)}
                  placeholder="+12025550123"
                  required
                  autoFocus
                  className="w-full bg-[#14171C] border border-[#2A303A] text-[#E8EAEE] focus:border-[#4C8DFF] focus:outline-none px-3.5 py-2 text-sm font-mono"
                />
              </div>

              <div className="flex gap-2.5">
                <button
                  type="button"
                  onClick={() => {
                    clearError()
                    setAuthStep('needs_keys')
                  }}
                  className="px-4 py-2.5 bg-[#242933] text-[#E8EAEE] hover:bg-[#2c3340] text-sm font-medium transition-colors duration-150"
                >
                  Back
                </button>
                <button
                  type="submit"
                  disabled={isLoading}
                  className="flex-1 py-2.5 px-4 bg-[#4C8DFF] text-[#ffffff] hover:bg-[#3b79e8] text-sm font-medium transition-colors duration-150 disabled:opacity-50"
                >
                  {isLoading ? 'Sending code...' : 'Send login code'}
                </button>
              </div>
            </form>
          )}

          {/* SCREEN 3: AUTO GENERATE - CODE STEP */}
          {authStep === 'auto_code' && (
            <form onSubmit={handleConfirmCode} className="space-y-6">
              <div className="space-y-2">
                <h1 className="text-xl font-semibold text-[#E8EAEE]">Enter confirmation code</h1>
                <p className="text-sm text-[#8B93A1] leading-relaxed">
                  Telegram will send a login code to your Telegram app. The code signs in to my.telegram.org to create your keys. It goes directly from this app to Telegram and is not stored.
                </p>
              </div>

              <div className="space-y-1">
                <label className="text-xs text-[#8B93A1] block">Login code</label>
                <input
                  type="text"
                  value={code}
                  onChange={(e) => setCode(e.target.value)}
                  placeholder="e.g. fH7xK92bA"
                  required
                  autoFocus
                  className="w-full bg-[#14171C] border border-[#2A303A] text-[#E8EAEE] focus:border-[#4C8DFF] focus:outline-none px-3.5 py-2 text-sm font-mono tracking-wider"
                />
              </div>

              <div className="flex gap-2.5">
                <button
                  type="button"
                  onClick={() => {
                    clearError()
                    setAuthStep('auto_phone')
                  }}
                  className="px-4 py-2.5 bg-[#242933] text-[#E8EAEE] hover:bg-[#2c3340] text-sm font-medium transition-colors duration-150"
                >
                  Back
                </button>
                <button
                  type="submit"
                  disabled={isLoading}
                  className="flex-1 py-2.5 px-4 bg-[#4C8DFF] text-[#ffffff] hover:bg-[#3b79e8] text-sm font-medium transition-colors duration-150 disabled:opacity-50"
                >
                  {isLoading ? 'Retrieving keys...' : 'Confirm and get keys'}
                </button>
              </div>

              <div className="pt-1 text-center">
                <button
                  type="button"
                  onClick={() => {
                    clearError()
                    setShowManualFields(true)
                    setAuthStep('needs_keys')
                  }}
                  className="text-xs text-[#4C8DFF] hover:underline"
                >
                  Enter keys manually instead
                </button>
              </div>
            </form>
          )}

          {/* SCREEN 4: QR CODE WAITING (HERO) */}
          {authStep === 'qr_waiting' && (
            <div className="space-y-6">
              {scannedUser ? (
                // Scanned user confirmation / channel setup
                <div className="space-y-5">
                  <div className="space-y-1">
                    <h1 className="text-xl font-semibold text-[#E8EAEE]">Welcome, {scannedUser.first_name || 'User'}</h1>
                    <p className="text-sm text-[#8B93A1]">Choose where your vault files should be stored.</p>
                  </div>

                  <div className="space-y-1">
                    <label className="text-xs text-[#8B93A1] block">Display name</label>
                    <input
                      type="text"
                      value={displayName}
                      onChange={(e) => setDisplayName(e.target.value)}
                      className="w-full bg-[#14171C] border border-[#2A303A] text-[#E8EAEE] focus:border-[#4C8DFF] focus:outline-none px-3.5 py-2 text-sm"
                    />
                  </div>

                  <div className="space-y-3 pt-2">
                    {availableChannels.length > 0 && (
                      <label className="flex items-start gap-3 p-3 bg-[#14171C] border border-[#2A303A] cursor-pointer">
                        <input
                          type="radio"
                          name="channel_action"
                          checked={selectedChannelAction === 'select_existing'}
                          onChange={() => setSelectedChannelAction('select_existing')}
                          className="mt-1"
                        />
                        <div className="flex-1 text-sm">
                          <span className="font-medium text-[#E8EAEE] block">Use existing channel</span>
                          <select
                            value={selectedChannelId}
                            onChange={(e) => {
                              setSelectedChannelId(e.target.value)
                              setSelectedChannelAction('select_existing')
                            }}
                            className="w-full bg-[#1B1F26] border border-[#2A303A] mt-2 px-2.5 py-1.5 text-xs text-[#E8EAEE] outline-none"
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

                    <label className="flex items-start gap-3 p-3 bg-[#14171C] border border-[#2A303A] cursor-pointer">
                      <input
                        type="radio"
                        name="channel_action"
                        checked={selectedChannelAction === 'auto_create'}
                        onChange={() => setSelectedChannelAction('auto_create')}
                        className="mt-1"
                      />
                      <div className="text-sm">
                        <span className="font-medium text-[#E8EAEE] block">Create new private channel</span>
                        <span className="text-xs text-[#8B93A1] block mt-0.5">
                          Creates a private channel named "{displayName || 'User'}'s Vayom Vault".
                        </span>
                      </div>
                    </label>
                  </div>

                  <button
                    type="button"
                    onClick={handleFinalize}
                    disabled={isLoading}
                    className="w-full py-2.5 px-4 bg-[#4C8DFF] text-[#ffffff] hover:bg-[#3b79e8] text-sm font-medium transition-colors duration-150 disabled:opacity-50"
                  >
                    {isLoading ? 'Setting up vault...' : 'Enter Vayom Drive'}
                  </button>
                </div>
              ) : (
                // Active QR Code Hero Presentation
                <div className="space-y-6">
                  <div className="space-y-1">
                    <h1 className="text-xl font-semibold text-[#E8EAEE]">Sign in with Telegram</h1>
                    <p className="text-sm text-[#8B93A1]">Scan the QR code with your mobile app to link this device.</p>
                  </div>

                  <div className="flex flex-col sm:flex-row items-center gap-6 pt-2">
                    {/* Hero QR Code: Large, dark on white with quiet zone */}
                    <div className="w-52 h-52 bg-[#ffffff] border border-[#2A303A] p-3 flex items-center justify-center flex-shrink-0">
                      {qrStatus === 'failed' ? (
                        <div className="text-center p-3 space-y-2">
                          <p className="text-xs text-[#E5534B]">Connection failed</p>
                          <button
                            type="button"
                            onClick={() => startQrSession({ mode: qrMode })}
                            className="px-3 py-1.5 bg-[#14171C] text-[#E8EAEE] text-xs font-medium hover:bg-[#242933] flex items-center gap-1 mx-auto"
                          >
                            <RefreshCw size={12} /> Retry
                          </button>
                        </div>
                      ) : qrUrl ? (
                        <QRCodeSVG
                          value={qrUrl}
                          size={180}
                          bgColor="#ffffff"
                          fgColor="#000000"
                          level="M"
                          includeMargin={false}
                        />
                      ) : (
                        <div className="text-xs text-[#8B93A1] animate-pulse">Generating code...</div>
                      )}
                    </div>

                    {/* Numbered Steps Sequence */}
                    <div className="space-y-3 flex-1 text-sm">
                      <ol className="space-y-2.5 text-[#E8EAEE] list-decimal list-inside text-sm leading-relaxed">
                        <li>Open Telegram on your phone</li>
                        <li>Go to Settings &gt; Devices</li>
                        <li>Tap Link Desktop Device</li>
                        <li>Scan this code</li>
                      </ol>

                      {qrStatus === 'failed' && (
                        <p className="text-xs text-[#E5534B]">
                          QR code could not be loaded. Check your internet connection and retry.
                        </p>
                      )}
                    </div>
                  </div>

                  {/* Mode indicator & honest warning */}
                  <div className="pt-2 border-t border-[#2A303A] space-y-2">
                    <div className="flex items-center justify-between text-xs text-[#8B93A1]">
                      <span>
                        {qrMode === 'own' ? (
                          <>Using your own API keys. {savedApiId ? `(#${savedApiId}) ` : ''}</>
                        ) : (
                          <>Using shared keys. </>
                        )}
                        <button
                          type="button"
                          onClick={() => {
                            clearError()
                            setShowManualFields(false)
                            setAuthStep('needs_keys')
                          }}
                          className="text-[#4C8DFF] hover:underline ml-1"
                        >
                          Edit
                        </button>
                      </span>

                      <button
                        type="button"
                        onClick={() => {
                          clearError()
                          setAuthStep('choosing')
                        }}
                        className="text-[#8B93A1] hover:text-[#E8EAEE]"
                      >
                        Change method
                      </button>
                    </div>

                    {qrMode === 'shared' && (
                      <p className="text-xs text-[#8B93A1] leading-relaxed">
                        Shared keys can hit Telegram rate limits sooner.
                      </p>
                    )}
                  </div>
                </div>
              )}
            </div>
          )}

          {/* SCREEN 5: 2FA PASSWORD */}
          {authStep === 'needs_2fa' && (
            <form onSubmit={handle2FASubmit} className="space-y-6">
              <div className="space-y-2">
                <h1 className="text-xl font-semibold text-[#E8EAEE]">Enter your Telegram password</h1>
                <p className="text-sm text-[#8B93A1]">Your account has two-step verification.</p>
              </div>

              <div className="space-y-1">
                <label className="text-xs text-[#8B93A1] block">Password</label>
                <input
                  type="password"
                  value={twoFactorPassword}
                  onChange={(e) => setTwoFactorPassword(e.target.value)}
                  placeholder="Two-step verification password"
                  required
                  autoFocus
                  className="w-full bg-[#14171C] border border-[#2A303A] text-[#E8EAEE] focus:border-[#4C8DFF] focus:outline-none px-3.5 py-2 text-sm"
                />
              </div>

              <div className="flex gap-2.5">
                <button
                  type="button"
                  onClick={() => {
                    clearError()
                    setAuthStep('qr_waiting')
                  }}
                  className="px-4 py-2.5 bg-[#242933] text-[#E8EAEE] hover:bg-[#2c3340] text-sm font-medium transition-colors duration-150"
                >
                  Back
                </button>
                <button
                  type="submit"
                  disabled={isLoading}
                  className="flex-1 py-2.5 px-4 bg-[#4C8DFF] text-[#ffffff] hover:bg-[#3b79e8] text-sm font-medium transition-colors duration-150 disabled:opacity-50"
                >
                  {isLoading ? 'Verifying...' : 'Submit password'}
                </button>
              </div>
            </form>
          )}

          {/* SCREEN 6: NAS FORM */}
          {authStep === 'nas_form' && (
            <form onSubmit={handleSaveNas} className="space-y-6">
              <div className="space-y-2">
                <h1 className="text-xl font-semibold text-[#E8EAEE]">Connect a NAS</h1>
                <p className="text-sm text-[#8B93A1] leading-relaxed">
                  Connect a local Network Attached Storage directory or folder. No Telegram account is required.
                </p>
              </div>

              <div className="space-y-4">
                <div className="space-y-1">
                  <label className="text-xs text-[#8B93A1] block">NAS vault root path</label>
                  <input
                    type="text"
                    value={nasPath}
                    onChange={(e) => {
                      setNasPath(e.target.value)
                      setNasTestResult(null)
                    }}
                    placeholder="/mnt/nas_vault or ./data/nas_vault"
                    required
                    autoFocus
                    className="w-full bg-[#14171C] border border-[#2A303A] text-[#E8EAEE] focus:border-[#4C8DFF] focus:outline-none px-3.5 py-2 text-sm font-mono"
                  />
                </div>

                <div className="space-y-1">
                  <label className="text-xs text-[#8B93A1] block">Vault name (optional)</label>
                  <input
                    type="text"
                    value={nasDisplayName}
                    onChange={(e) => setNasDisplayName(e.target.value)}
                    placeholder="e.g. Home Vault"
                    className="w-full bg-[#14171C] border border-[#2A303A] text-[#E8EAEE] focus:border-[#4C8DFF] focus:outline-none px-3.5 py-2 text-sm"
                  />
                </div>

                {/* Test Connection Button & Result */}
                <div>
                  <button
                    type="button"
                    onClick={handleTestNas}
                    disabled={isLoading}
                    className="px-3.5 py-2 bg-[#242933] text-[#E8EAEE] hover:bg-[#2c3340] text-xs font-medium transition-colors duration-150"
                  >
                    Test connection
                  </button>

                  {nasTestResult?.tested && (
                    <div className="mt-2 text-xs">
                      {nasTestResult.success ? (
                        <div className="text-[#38bdf8] flex items-center gap-1.5">
                          <CheckCircle2 size={14} />
                          <span>{nasTestResult.message}</span>
                        </div>
                      ) : (
                        <div className="text-[#E5534B] flex items-start gap-1.5">
                          <AlertCircle size={14} className="flex-shrink-0 mt-0.5" />
                          <span>{nasTestResult.error}</span>
                        </div>
                      )}
                    </div>
                  )}
                </div>
              </div>

              <div className="flex gap-2.5 pt-2">
                <button
                  type="button"
                  onClick={() => {
                    clearError()
                    setAuthStep('choosing')
                  }}
                  className="px-4 py-2.5 bg-[#242933] text-[#E8EAEE] hover:bg-[#2c3340] text-sm font-medium transition-colors duration-150"
                >
                  Back
                </button>
                <button
                  type="submit"
                  disabled={isLoading}
                  className="flex-1 py-2.5 px-4 bg-[#4C8DFF] text-[#ffffff] hover:bg-[#3b79e8] text-sm font-medium transition-colors duration-150 disabled:opacity-50"
                >
                  {isLoading ? 'Connecting...' : 'Connect NAS'}
                </button>
              </div>
            </form>
          )}

          {/* SCREEN 7: BOT & CHANNEL FORM */}
          {authStep === 'bot_form' && (
            <form onSubmit={handleBotSubmit} className="space-y-5">
              <div className="space-y-2">
                <h1 className="text-xl font-semibold text-[#E8EAEE]">Telegram bot and channel</h1>
                <p className="text-sm text-[#8B93A1] leading-relaxed">
                  Connect using a dedicated Telegram bot token created via{' '}
                  <a
                    href="https://t.me/BotFather"
                    target="_blank"
                    rel="noreferrer"
                    className="text-[#4C8DFF] hover:underline inline-flex items-center gap-1"
                  >
                    @BotFather <ExternalLink size={11} />
                  </a>.
                </p>
              </div>

              <div className="space-y-3">
                <div className="space-y-1">
                  <label className="text-xs text-[#8B93A1] block">Bot token</label>
                  <input
                    type="text"
                    value={botToken}
                    onChange={(e) => setBotToken(e.target.value)}
                    placeholder="e.g. 7123456789:AAFx..."
                    required
                    autoFocus
                    className="w-full bg-[#14171C] border border-[#2A303A] text-[#E8EAEE] focus:border-[#4C8DFF] focus:outline-none px-3.5 py-2 text-sm font-mono"
                  />
                </div>

                <div className="space-y-1">
                  <label className="text-xs text-[#8B93A1] block">Channel ID</label>
                  <input
                    type="text"
                    value={botChannelId}
                    onChange={(e) => setBotChannelId(e.target.value)}
                    placeholder="e.g. -1002345678901"
                    required
                    className="w-full bg-[#14171C] border border-[#2A303A] text-[#E8EAEE] focus:border-[#4C8DFF] focus:outline-none px-3.5 py-2 text-sm font-mono"
                  />
                </div>

                <div className="space-y-1">
                  <label className="text-xs text-[#8B93A1] block">Display name (optional)</label>
                  <input
                    type="text"
                    value={botDisplayName}
                    onChange={(e) => setBotDisplayName(e.target.value)}
                    placeholder="e.g. My Storage Bot"
                    className="w-full bg-[#14171C] border border-[#2A303A] text-[#E8EAEE] focus:border-[#4C8DFF] focus:outline-none px-3.5 py-2 text-sm"
                  />
                </div>
              </div>

              <div className="flex gap-2.5 pt-2">
                <button
                  type="button"
                  onClick={() => {
                    clearError()
                    setAuthStep('choosing')
                  }}
                  className="px-4 py-2.5 bg-[#242933] text-[#E8EAEE] hover:bg-[#2c3340] text-sm font-medium transition-colors duration-150"
                >
                  Back
                </button>
                <button
                  type="submit"
                  disabled={isLoading}
                  className="flex-1 py-2.5 px-4 bg-[#4C8DFF] text-[#ffffff] hover:bg-[#3b79e8] text-sm font-medium transition-colors duration-150 disabled:opacity-50"
                >
                  {isLoading ? 'Connecting...' : 'Connect storage bot'}
                </button>
              </div>

              {devModeEnabled && (
                <div className="pt-2 text-center">
                  <button
                    type="button"
                    onClick={() => {
                      clearError()
                      setAuthStep('dev_form')
                    }}
                    className="text-xs text-[#8B93A1] hover:text-[#E8EAEE]"
                  >
                    Switch to Developer mode
                  </button>
                </div>
              )}
            </form>
          )}

          {/* SCREEN 8: DEVELOPER MODE FORM (VISIBLE ONLY IN DEV MODE) */}
          {authStep === 'dev_form' && devModeEnabled && (
            <form onSubmit={handleDevSubmit} className="space-y-5">
              <div className="space-y-2">
                <h1 className="text-xl font-semibold text-[#E8EAEE]">Developer mode</h1>
                <p className="text-sm text-[#8B93A1] leading-relaxed">
                  Provide custom API credentials, bot token, and storage channel ID.
                </p>
              </div>

              <div className="space-y-3">
                <div className="grid grid-cols-2 gap-3">
                  <div className="space-y-1">
                    <label className="text-xs text-[#8B93A1] block">API ID</label>
                    <input
                      type="number"
                      value={devApiId}
                      onChange={(e) => setDevApiId(e.target.value)}
                      placeholder="e.g. 28471920"
                      required
                      autoFocus
                      className="w-full bg-[#14171C] border border-[#2A303A] text-[#E8EAEE] focus:border-[#4C8DFF] focus:outline-none px-3.5 py-2 text-sm font-mono"
                    />
                  </div>
                  <div className="space-y-1">
                    <label className="text-xs text-[#8B93A1] block">API Hash</label>
                    <input
                      type="text"
                      value={devApiHash}
                      onChange={(e) => setDevApiHash(e.target.value)}
                      placeholder="32-char hex"
                      required
                      className="w-full bg-[#14171C] border border-[#2A303A] text-[#E8EAEE] focus:border-[#4C8DFF] focus:outline-none px-3.5 py-2 text-sm font-mono"
                    />
                  </div>
                </div>

                <div className="space-y-1">
                  <label className="text-xs text-[#8B93A1] block">Bot token</label>
                  <input
                    type="text"
                    value={devBotToken}
                    onChange={(e) => setDevBotToken(e.target.value)}
                    placeholder="HTTP bot token"
                    required
                    className="w-full bg-[#14171C] border border-[#2A303A] text-[#E8EAEE] focus:border-[#4C8DFF] focus:outline-none px-3.5 py-2 text-sm font-mono"
                  />
                </div>

                <div className="space-y-1">
                  <label className="text-xs text-[#8B93A1] block">Channel ID</label>
                  <input
                    type="text"
                    value={devChannelId}
                    onChange={(e) => setDevChannelId(e.target.value)}
                    placeholder="e.g. -1002345678901"
                    required
                    className="w-full bg-[#14171C] border border-[#2A303A] text-[#E8EAEE] focus:border-[#4C8DFF] focus:outline-none px-3.5 py-2 text-sm font-mono"
                  />
                </div>

                <div className="space-y-1">
                  <label className="text-xs text-[#8B93A1] block">Display name</label>
                  <input
                    type="text"
                    value={devDisplayName}
                    onChange={(e) => setDevDisplayName(e.target.value)}
                    placeholder="e.g. Developer Node"
                    className="w-full bg-[#14171C] border border-[#2A303A] text-[#E8EAEE] focus:border-[#4C8DFF] focus:outline-none px-3.5 py-2 text-sm"
                  />
                </div>
              </div>

              <div className="flex gap-2.5 pt-2">
                <button
                  type="button"
                  onClick={() => {
                    clearError()
                    setAuthStep('bot_form')
                  }}
                  className="px-4 py-2.5 bg-[#242933] text-[#E8EAEE] hover:bg-[#2c3340] text-sm font-medium transition-colors duration-150"
                >
                  Back
                </button>
                <button
                  type="submit"
                  disabled={isLoading}
                  className="flex-1 py-2.5 px-4 bg-[#4C8DFF] text-[#ffffff] hover:bg-[#3b79e8] text-sm font-medium transition-colors duration-150 disabled:opacity-50"
                >
                  {isLoading ? 'Connecting...' : 'Connect developer node'}
                </button>
              </div>
            </form>
          )}
        </div>
      </main>

      {/* Footer */}
      <footer className="h-10 px-6 flex items-center text-xs text-[#8B93A1] border-t border-[#2A303A]">
        <span>Vayom Drive v1.0.0</span>
      </footer>
    </div>
  )
}
