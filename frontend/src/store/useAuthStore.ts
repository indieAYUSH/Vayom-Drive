import { create } from 'zustand'

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

interface AuthState {
  isAuthenticated: boolean
  isInitializing: boolean
  isLoading: boolean
  authMode: 'qr' | 'bot' | 'developer' | ''
  user: UserProfile | null
  channelId: string | null

  // QR Session State
  qrSessionId: string | null
  qrUrl: string | null
  qrExpiresAt: number | null
  qrStatus: 'idle' | 'waiting' | 'confirmed' | '2fa_required' | 'expired' | 'failed'
  scannedUser: UserProfile | null
  availableChannels: ChannelOption[]
  defaultChannelId: string | null
  error: string | null

  // Session Expiry
  sessionExpired: boolean
  handleSessionExpired: () => void

  // Actions
  checkAuth: () => Promise<boolean>
  startQrSession: () => Promise<void>
  pollQrSession: () => Promise<void>
  submit2FA: (password: string) => Promise<void>
  finalizeQr: (displayName: string, action: 'auto_create' | 'select_existing', channelId?: string) => Promise<void>
  loginWithBot: (botToken: string, channelId: string, displayName?: string) => Promise<void>
  loginWithDeveloper: (apiId: number, apiHash: string, botToken: string, channelId: string, displayName?: string) => Promise<void>
  logout: () => Promise<void>
  clearError: () => void
}

let pollInterval: any = null
let currentQrRequestId = 0
let qrAbortController: AbortController | null = null

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
  user: null,
  channelId: null,

  qrSessionId: null,
  qrUrl: null,
  qrExpiresAt: null,
  qrStatus: 'idle',
  scannedUser: null,
  availableChannels: [],
  defaultChannelId: null,
  error: null,
  sessionExpired: false,

  checkAuth: async () => {
    try {
      const res = await fetch('/api/v1/auth/status')
      if (!res.ok) throw new Error('Failed to verify authentication status')
      const data = await res.json()
      set({
        isAuthenticated: data.is_authenticated,
        authMode: data.auth_mode || '',
        user: data.user,
        channelId: data.channel_id,
        isInitializing: false,
      })
      return data.is_authenticated
    } catch (err: any) {
      set({ isAuthenticated: false, isInitializing: false, error: null })
      return false
    }
  },

  startQrSession: async () => {
    // H-4: Abort any in-flight start request and clear active intervals
    stopQrPolling()

    const requestId = ++currentQrRequestId
    qrAbortController = new AbortController()

    set({ qrStatus: 'waiting', error: null, scannedUser: null, availableChannels: [] })
    try {
      const res = await fetch('/api/v1/auth/qr/start', {
        method: 'POST',
        signal: qrAbortController.signal,
      })
      if (requestId !== currentQrRequestId) return
      if (!res.ok) throw new Error('Could not initialize Telegram QR session')
      const data = await res.json()

      if (requestId !== currentQrRequestId) return

      set({
        qrSessionId: data.session_id,
        qrUrl: data.url,
        qrExpiresAt: data.expires_at,
        qrStatus: 'waiting',
      })

      // H-4: Clean any existing interval right before starting the new one
      if (pollInterval) {
        clearInterval(pollInterval)
        pollInterval = null
      }
      pollInterval = setInterval(() => {
        get().pollQrSession()
      }, 2500)
    } catch (err: any) {
      if (err.name === 'AbortError') return
      if (requestId !== currentQrRequestId) return
      set({ qrStatus: 'failed', error: err.message || 'Failed to start QR login' })
    } finally {
      if (requestId === currentQrRequestId) {
        qrAbortController = null
      }
    }
  },

  pollQrSession: async () => {
    const { qrSessionId, qrStatus } = get()
    if (!qrSessionId || qrStatus === 'confirmed' || qrStatus === 'expired' || qrStatus === 'failed') {
      stopQrPolling()
      return
    }

    try {
      const res = await fetch(`/api/v1/auth/qr/poll?session_id=${qrSessionId}`)
      if (!res.ok) {
        if (res.status === 404) {
          set({ qrStatus: 'expired' })
          stopQrPolling()
        }
        return
      }

      const data = await res.json()
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
        set({ qrStatus: '2fa_required' })
      } else if (data.status === 'expired') {
        stopQrPolling()
        set({ qrStatus: 'expired' })
      } else if (data.status === 'waiting') {
        // Token seamlessly auto-refreshed by backend
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
    } catch (err) {
      // transient network error, keep polling
    }
  },

  submit2FA: async (password: string) => {
    const { qrSessionId } = get()
    if (!qrSessionId) return
    set({ isLoading: true, error: null })
    try {
      const res = await fetch('/api/v1/auth/qr/2fa', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ session_id: qrSessionId, password }),
      })
      if (!res.ok) {
        const errData = await res.json().catch(() => ({}))
        throw new Error(errData.detail || 'Incorrect 2FA password')
      }
      // Re-poll to fetch user and channels
      await get().pollQrSession()
      set({ isLoading: false })
    } catch (err: any) {
      set({ isLoading: false, error: err.message || 'Failed 2FA verification' })
      throw err
    }
  },

  finalizeQr: async (displayName, action, channelId) => {
    const { qrSessionId } = get()
    if (!qrSessionId) return
    set({ isLoading: true, error: null })
    try {
      const res = await fetch('/api/v1/auth/qr/finalize', {
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
        authMode: 'qr',
        channelId: data.channel_id,
        user: data.user,
        isLoading: false,
        qrSessionId: null,
        qrUrl: null,
      })
    } catch (err: any) {
      set({ isLoading: false, error: err.message })
      throw err
    }
  },

  loginWithBot: async (botToken, channelId, displayName) => {
    // H-3: Stop any active QR polling interval when logging in via Bot token
    stopQrPolling()
    set({ isLoading: true, error: null })
    try {
      const res = await fetch('/api/v1/auth/bot', {
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
      })
    } catch (err: any) {
      set({ isLoading: false, error: err.message })
      throw err
    }
  },

  loginWithDeveloper: async (apiId, apiHash, botToken, channelId, displayName) => {
    // H-3: Stop any active QR polling interval when logging in via Developer mode
    stopQrPolling()
    set({ isLoading: true, error: null })
    try {
      const res = await fetch('/api/v1/auth/developer', {
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
      await fetch('/api/v1/auth/logout', { method: 'POST' })
    } catch (e) {
      // ignore
    }
    set({
      isAuthenticated: false,
      authMode: '',
      user: null,
      channelId: null,
      qrSessionId: null,
      qrUrl: null,
      qrExpiresAt: null,
      qrStatus: 'idle',
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
      user: null,
      channelId: null,
      qrSessionId: null,
      qrUrl: null,
      qrExpiresAt: null,
      qrStatus: 'idle',
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
