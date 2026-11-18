import { create } from 'zustand'
import { API_BASE } from '../utils/apiUrl'

export interface UserProfile {
  display_name: string
  first_name?: string
  last_name?: string
  username?: string
  id?: number
  is_premium?: boolean
}

export interface ChannelOption {
  id: string
  title: string
  is_creator: boolean
}

export type AuthStep =
  | 'choosing'
  | 'needs_keys'
  | 'auto_phone'
  | 'auto_code'
  | 'qr_waiting'
  | 'needs_2fa'
  | 'nas_form'
  | 'bot_form'
  | 'dev_form'
  | 'connected'

export type QrMode = 'own' | 'shared'

interface AuthState {
  isAuthenticated: boolean
  isInitializing: boolean
  isLoading: boolean
  authMode: 'qr' | 'byok_qr' | 'bot' | 'developer' | 'nas' | ''
  authStep: AuthStep
  qrMode: QrMode
  user: UserProfile | null
  channelId: string | null

  // Saved credentials info (server-side, masked)
  hasSavedKeys: boolean
  savedApiId: number | null
  savedApiHashMasked: string | null
  devModeEnabled: boolean

  // QR Session State
  qrSessionId: string | null
  qrUrl: string | null
  qrExpiresAt: number | null
  qrStatus: 'idle' | 'waiting' | 'confirmed' | '2fa_required' | 'expired' | 'failed'
  byokCreds: { apiId?: number; apiHash?: string } | null
  scannedUser: UserProfile | null
  availableChannels: ChannelOption[]
  defaultChannelId: string | null
  error: string | null

  // Session Expiry
  sessionExpired: boolean
  handleSessionExpired: () => void

  // Navigation & Form State Actions
  setAuthStep: (step: AuthStep) => void
  setQrMode: (mode: QrMode) => void
  clearSensitiveFormState: () => void

  // Actions
  checkAuth: () => Promise<boolean>
  saveManualKeys: (apiId: number, apiHash: string) => Promise<void>
  requestByokCode: (phone: string) => Promise<string>
  extractByokKeys: (phone: string, randomHash: string, code: string) => Promise<{ apiId: number; apiHash?: string }>
  startQrSession: (options?: { mode?: QrMode; apiId?: number; apiHash?: string }) => Promise<void>
  pollQrSession: () => Promise<void>
  submit2FA: (password: string) => Promise<void>
  finalizeQr: (displayName: string, action: 'auto_create' | 'select_existing', channelId?: string) => Promise<void>
  testNasConnection: (vaultRoot?: string) => Promise<{ success: boolean; error?: string; message?: string; free_bytes?: number; total_bytes?: number }>
  saveNasConfig: (vaultRoot: string, displayName?: string) => Promise<void>
  loginWithBot: (botToken: string, channelId: string, displayName?: string) => Promise<void>
  loginWithDeveloper: (apiId: number, apiHash: string, botToken: string, channelId: string, displayName?: string) => Promise<void>
  logout: () => Promise<void>
  clearError: () => void
}

let pollInterval: any = null
let currentQrRequestId = 0
let qrAbortController: AbortController | null = null
let qrPollFailures = 0

const QR_START_TIMEOUT_MS = 45_000
const QR_POLL_FAILURE_LIMIT = 12

const AUTH_PROBE_MAX_ATTEMPTS = 24
const AUTH_PROBE_DELAY_MS = 500

const sleep = (ms: number) => new Promise((resolve) => setTimeout(resolve, ms))

export function stopQrPolling() {
  if (qrAbortController) {
    qrAbortController.abort()
    qrAbortController = null
  }
  if (pollInterval) {
    clearInterval(pollInterval)
    pollInterval = null
  }
}

export const useAuthStore = create<AuthState>((set, get) => ({
  isAuthenticated: false,
  isInitializing: true,
  isLoading: false,
  authMode: '',
  authStep: 'choosing',
  qrMode: 'own',
  user: null,
  channelId: null,

  hasSavedKeys: false,
  savedApiId: null,
  savedApiHashMasked: null,
  devModeEnabled: false,

  qrSessionId: null,
  qrUrl: null,
  qrExpiresAt: null,
  qrStatus: 'idle',
  byokCreds: null,
  scannedUser: null,
  availableChannels: [],
  defaultChannelId: null,
  error: null,
  sessionExpired: false,

  setAuthStep: (step: AuthStep) => set({ authStep: step, error: null }),
  setQrMode: (mode: QrMode) => set({ qrMode: mode }),

  clearSensitiveFormState: () => {
    set({
      byokCreds: null,
      error: null,
    })
  },

  checkAuth: async () => {
    for (let attempt = 1; attempt <= AUTH_PROBE_MAX_ATTEMPTS; attempt++) {
      try {
        const res = await fetch(`${API_BASE}/auth/status`)
        if (!res.ok) {
          set({ isAuthenticated: false, isInitializing: false, error: null, authStep: 'choosing' })
          return false
        }

        const data = await res.json()
        const isAuth = Boolean(data.is_authenticated)
        const hasSaved = Boolean(data.has_saved_keys)

        set({
          isAuthenticated: isAuth,
          authMode: data.auth_mode || '',
          user: data.user,
          channelId: data.channel_id,
          hasSavedKeys: hasSaved,
          savedApiId: data.saved_api_id || null,
          savedApiHashMasked: data.saved_api_hash_masked || null,
          devModeEnabled: Boolean(data.dev_mode_enabled),
          isInitializing: false,
          authStep: isAuth ? 'connected' : hasSaved ? 'qr_waiting' : 'choosing',
          qrMode: 'own',
        })

        // Returning user with saved keys but no session: go straight to qr_waiting
        if (!isAuth && hasSaved) {
          get().startQrSession({ mode: 'own' })
        }

        return isAuth
      } catch (err: any) {
        if (attempt >= AUTH_PROBE_MAX_ATTEMPTS) {
          set({ isAuthenticated: false, isInitializing: false, error: null, authStep: 'choosing' })
          return false
        }
        await sleep(AUTH_PROBE_DELAY_MS)
      }
    }
    set({ isAuthenticated: false, isInitializing: false, error: null, authStep: 'choosing' })
    return false
  },

  saveManualKeys: async (apiId: number, apiHash: string) => {
    set({ isLoading: true, error: null })
    try {
      const res = await fetch(`${API_BASE}/auth/keys`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ api_id: apiId, api_hash: apiHash.trim() }),
      })
      const data = await res.json()
      if (!res.ok) {
        throw new Error(data?.detail || 'Failed to save Telegram API keys')
      }
      set({
        hasSavedKeys: true,
        savedApiId: data.api_id,
        savedApiHashMasked: data.api_hash_masked,
        qrMode: 'own',
        isLoading: false,
      })
    } catch (err: any) {
      set({ isLoading: false, error: err.message })
      throw err
    }
  },

  requestByokCode: async (phone: string) => {
    set({ isLoading: true, error: null })
    try {
      const res = await fetch(`${API_BASE}/auth/byok/request-code`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ phone }),
      })
      const data = await res.json()
      if (!res.ok) {
        throw new Error(data?.detail || 'Failed to request Telegram confirmation code')
      }
      return data.random_hash
    } catch (err: any) {
      set({ error: err.message })
      throw err
    } finally {
      set({ isLoading: false })
    }
  },

  extractByokKeys: async (phone: string, randomHash: string, code: string) => {
    set({ isLoading: true, error: null })
    try {
      const res = await fetch(`${API_BASE}/auth/byok/extract-keys`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ phone, random_hash: randomHash, code }),
      })
      const data = await res.json()
      if (!res.ok) {
        throw new Error(data?.detail || 'Failed to extract Telegram API credentials')
      }
      set({
        hasSavedKeys: true,
        savedApiId: Number(data.api_id),
        savedApiHashMasked: data.api_hash_masked || (data.api_hash ? `...${data.api_hash.slice(-4)}` : null),
        qrMode: 'own',
      })
      return { apiId: Number(data.api_id), apiHash: data.api_hash }
    } catch (err: any) {
      set({ error: err.message })
      throw err
    } finally {
      set({ isLoading: false })
    }
  },

  startQrSession: async (options) => {
    stopQrPolling()

    const requestId = ++currentQrRequestId
    const controller = new AbortController()
    qrAbortController = controller
    qrPollFailures = 0

    const targetMode: QrMode = options?.mode || get().qrMode || 'own'
    set({
      qrMode: targetMode,
      qrStatus: 'waiting',
      error: null,
      scannedUser: null,
      availableChannels: [],
      qrSessionId: null,
      qrUrl: null,
      qrExpiresAt: null,
      authStep: 'qr_waiting',
    })

    const timeoutId = setTimeout(() => controller.abort(), QR_START_TIMEOUT_MS)
    try {
      const payload: any = { mode: targetMode }
      if (targetMode === 'own') {
        if (options?.apiId && options?.apiHash) {
          payload.api_id = options.apiId
          payload.api_hash = options.apiHash
        } else if (get().savedApiId) {
          payload.api_id = get().savedApiId
        }
      }

      const res = await fetch(`${API_BASE}/auth/qr/start`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
        signal: controller.signal,
      })

      if (requestId !== currentQrRequestId) return

      if (!res.ok) {
        let message = 'Could not initialize Telegram QR session'
        try {
          const body = await res.json()
          if (typeof body?.detail === 'string' && body.detail.trim()) {
            message = body.detail
          }
        } catch {}
        throw new Error(message)
      }

      const data = await res.json()
      if (requestId !== currentQrRequestId) return

      set({
        qrSessionId: data.session_id,
        qrUrl: data.url,
        qrExpiresAt: data.expires_at,
        qrStatus: 'waiting',
      })

      if (pollInterval) {
        clearInterval(pollInterval)
        pollInterval = null
      }
      pollInterval = setInterval(() => {
        get().pollQrSession()
      }, 2500)
    } catch (err: any) {
      if (requestId !== currentQrRequestId) return
      if (err.name === 'AbortError') {
        set({
          qrStatus: 'failed',
          error: 'Starting the QR session timed out. Check your internet connection and try again.',
        })
        return
      }
      set({ qrStatus: 'failed', error: err.message || 'Failed to start QR login' })
    } finally {
      clearTimeout(timeoutId)
      if (requestId === currentQrRequestId) {
        qrAbortController = null
      }
    }
  },

  pollQrSession: async () => {
    const sessionId = get().qrSessionId
    const qrStatus = get().qrStatus
    if (!sessionId || qrStatus === 'confirmed' || qrStatus === 'expired' || qrStatus === 'failed') {
      stopQrPolling()
      return
    }

    try {
      const res = await fetch(`${API_BASE}/auth/qr/poll?session_id=${sessionId}`)
      if (get().qrSessionId !== sessionId) return

      if (!res.ok) {
        if (res.status === 404) {
          set({ qrStatus: 'expired' })
          stopQrPolling()
        } else {
          qrPollFailures += 1
          if (qrPollFailures >= QR_POLL_FAILURE_LIMIT) {
            stopQrPolling()
            set({
              qrStatus: 'failed',
              error: 'Lost connection to the local backend while waiting for Telegram. Try again.',
            })
          }
        }
        return
      }

      const data = await res.json()
      if (get().qrSessionId !== sessionId) return
      qrPollFailures = 0

      if (data.status === 'confirmed') {
        stopQrPolling()
        set({
          qrStatus: 'confirmed',
          scannedUser: data.user,
          availableChannels: data.channels || [],
          defaultChannelId: data.default_channel_id || null,
        })
      } else if (data.status === '2fa_required') {
        stopQrPolling()
        set({ qrStatus: '2fa_required', authStep: 'needs_2fa' })
      } else if (data.status === 'expired') {
        stopQrPolling()
        set({ qrStatus: 'expired' })
      } else if (data.status === 'waiting') {
        // Silent token refresh
        const updates: any = {}
        if (data.url && data.url !== get().qrUrl) {
          updates.qrUrl = data.url
        }
        if (data.expires_at && data.expires_at !== get().qrExpiresAt) {
          updates.qrExpiresAt = data.expires_at
        }
        if (Object.keys(updates).length > 0) {
          set(updates)
        }
      }
    } catch {
      if (get().qrSessionId !== sessionId) return
      qrPollFailures += 1
      if (qrPollFailures >= QR_POLL_FAILURE_LIMIT && get().qrStatus === 'waiting') {
        stopQrPolling()
        set({
          qrStatus: 'failed',
          error: 'Lost connection to the local backend while waiting for Telegram. Try again.',
        })
      }
    }
  },

  submit2FA: async (password: string) => {
    const { qrSessionId } = get()
    if (!qrSessionId) return
    set({ isLoading: true, error: null })
    try {
      const res = await fetch(`${API_BASE}/auth/qr/2fa`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ session_id: qrSessionId, password }),
      })
      if (!res.ok) {
        const errData = await res.json().catch(() => ({}))
        throw new Error(errData.detail || 'Invalid two-step verification password.')
      }
      // Re-poll to fetch user and channels
      await get().pollQrSession()
      set({ isLoading: false })
    } catch (err: any) {
      set({ isLoading: false, error: err.message || 'Failed two-step verification' })
      throw err
    }
  },

  finalizeQr: async (displayName, action, channelId) => {
    const { qrSessionId } = get()
    if (!qrSessionId) return
    set({ isLoading: true, error: null })
    try {
      const res = await fetch(`${API_BASE}/auth/qr/finalize`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          session_id: qrSessionId,
          display_name: displayName,
          action,
          selected_channel_id: channelId || undefined,
        }),
      })
      if (!res.ok) {
        const errData = await res.json().catch(() => ({}))
        throw new Error(errData.detail || 'Failed to finalize setup')
      }
      const data = await res.json()
      stopQrPolling()
      set({
        isAuthenticated: true,
        authMode: get().qrMode === 'own' ? 'byok_qr' : 'qr',
        channelId: data.channel_id,
        user: data.user,
        isLoading: false,
        qrSessionId: null,
        qrUrl: null,
        authStep: 'connected',
      })
    } catch (err: any) {
      set({ isLoading: false, error: err.message })
      throw err
    }
  },

  testNasConnection: async (vaultRoot?: string) => {
    set({ isLoading: true, error: null })
    try {
      const res = await fetch(`${API_BASE}/auth/nas/test`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ vault_root: vaultRoot?.trim() || undefined }),
      })
      const data = await res.json()
      return data
    } catch (err: any) {
      return { success: false, error: err.message || 'Connection test failed' }
    } finally {
      set({ isLoading: false })
    }
  },

  saveNasConfig: async (vaultRoot: string, displayName?: string) => {
    set({ isLoading: true, error: null })
    try {
      const res = await fetch(`${API_BASE}/auth/nas/save`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          vault_root: vaultRoot.trim(),
          display_name: displayName?.trim() || 'NAS Vault',
        }),
      })
      if (!res.ok) {
        const errData = await res.json().catch(() => ({}))
        throw new Error(errData.detail || 'Failed to save NAS configuration')
      }
      const data = await res.json()
      set({
        isAuthenticated: true,
        authMode: 'nas',
        channelId: 'nas_vault',
        user: data.user,
        isLoading: false,
        authStep: 'connected',
      })
    } catch (err: any) {
      set({ isLoading: false, error: err.message })
      throw err
    }
  },

  loginWithBot: async (botToken, channelId, displayName) => {
    stopQrPolling()
    set({ isLoading: true, error: null })
    try {
      const res = await fetch(`${API_BASE}/auth/bot`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          bot_token: botToken.trim(),
          channel_id: channelId.trim(),
          display_name: displayName?.trim() || 'Storage Bot',
        }),
      })
      if (!res.ok) {
        const errData = await res.json().catch(() => ({}))
        throw new Error(errData.detail || 'Failed to authenticate with Bot Token')
      }
      const data = await res.json()
      stopQrPolling()
      set({
        isAuthenticated: true,
        authMode: 'bot',
        channelId: data.channel_id,
        user: { display_name: displayName || 'Storage Bot', first_name: 'Bot' },
        isLoading: false,
        qrSessionId: null,
        qrUrl: null,
        qrStatus: 'idle',
        authStep: 'connected',
      })
    } catch (err: any) {
      set({ isLoading: false, error: err.message })
      throw err
    }
  },

  loginWithDeveloper: async (apiId, apiHash, botToken, channelId, displayName) => {
    stopQrPolling()
    set({ isLoading: true, error: null })
    try {
      const res = await fetch(`${API_BASE}/auth/developer`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          api_id: Number(apiId),
          api_hash: apiHash.trim(),
          bot_token: botToken.trim(),
          channel_id: channelId.trim(),
          display_name: displayName?.trim() || 'Developer Node',
        }),
      })
      if (!res.ok) {
        const errData = await res.json().catch(() => ({}))
        throw new Error(errData.detail || 'Failed to connect with custom credentials')
      }
      const data = await res.json()
      stopQrPolling()
      set({
        isAuthenticated: true,
        authMode: 'developer',
        channelId: data.channel_id,
        user: { display_name: displayName || 'Developer Node', first_name: 'Custom MTProto' },
        isLoading: false,
        qrSessionId: null,
        qrUrl: null,
        qrStatus: 'idle',
        authStep: 'connected',
      })
    } catch (err: any) {
      set({ isLoading: false, error: err.message })
      throw err
    }
  },

  logout: async () => {
    stopQrPolling()
    set({ isLoading: true })
    try {
      await fetch(`${API_BASE}/auth/logout`, { method: 'POST' })
    } catch {}
    set({
      isAuthenticated: false,
      authMode: '',
      authStep: 'choosing',
      qrMode: 'own',
      user: null,
      channelId: null,
      qrSessionId: null,
      qrUrl: null,
      qrExpiresAt: null,
      qrStatus: 'idle',
      byokCreds: null,
      scannedUser: null,
      availableChannels: [],
      defaultChannelId: null,
      error: null,
      isLoading: false,
    })
  },

  handleSessionExpired: () => {
    stopQrPolling()
    set({
      isAuthenticated: false,
      authMode: '',
      authStep: 'choosing',
      user: null,
      channelId: null,
      qrSessionId: null,
      qrUrl: null,
      qrExpiresAt: null,
      qrStatus: 'idle',
      byokCreds: null,
      scannedUser: null,
      availableChannels: [],
      defaultChannelId: null,
      sessionExpired: true,
      error: 'Session expired. Please log in again to resume access.',
      isLoading: false,
    })
  },

  clearError: () => set({ error: null, sessionExpired: false }),
}))
