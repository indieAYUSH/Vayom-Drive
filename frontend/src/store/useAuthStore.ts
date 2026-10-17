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
  isLoading: boolean
  authMode: 'qr' | 'bot' | 'developer' | ''
  user: UserProfile | null
  channelId: string | null

  // QR Session State
  qrSessionId: string | null
  qrUrl: string | null
  qrExpiresAt: number | null
  qrStatus: 'idle' | 'waiting' | 'confirmed' | '2fa_required' | 'expired'
  scannedUser: UserProfile | null
  availableChannels: ChannelOption[]
  defaultChannelId: string | null
  error: string | null

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

export const useAuthStore = create<AuthState>((set, get) => ({
  isAuthenticated: false,
  isLoading: true,
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

  checkAuth: async () => {
    set({ isLoading: true })
    try {
      const res = await fetch('/api/v1/auth/status')
      if (!res.ok) throw new Error('Failed to verify authentication status')
      const data = await res.json()
      set({
        isAuthenticated: data.is_authenticated,
        authMode: data.auth_mode || '',
        user: data.user,
        channelId: data.channel_id,
        isLoading: false,
      })
      return data.is_authenticated
    } catch (err: any) {
      set({ isAuthenticated: false, isLoading: false, error: err.message })
      return false
    }
  },

  startQrSession: async () => {
    if (pollInterval) {
      clearInterval(pollInterval)
      pollInterval = null
    }

    set({ qrStatus: 'waiting', error: null, scannedUser: null, availableChannels: [] })
    try {
      const res = await fetch('/api/v1/auth/qr/start', { method: 'POST' })
      if (!res.ok) throw new Error('Could not initialize Telegram QR session')
      const data = await res.json()

      set({
        qrSessionId: data.session_id,
        qrUrl: data.url,
        qrExpiresAt: data.expires_at,
        qrStatus: 'waiting',
      })

      // Start automatic polling every 2.5 seconds
      pollInterval = setInterval(() => {
        get().pollQrSession()
      }, 2500)
    } catch (err: any) {
      set({ qrStatus: 'idle', error: err.message || 'Failed to start QR login' })
    }
  },

  pollQrSession: async () => {
    const { qrSessionId, qrStatus } = get()
    if (!qrSessionId || qrStatus === 'confirmed' || qrStatus === 'expired') {
      if (pollInterval) clearInterval(pollInterval)
      return
    }

    try {
      const res = await fetch(`/api/v1/auth/qr/poll?session_id=${qrSessionId}`)
      if (!res.ok) {
        if (res.status === 404) {
          set({ qrStatus: 'expired' })
          if (pollInterval) clearInterval(pollInterval)
        }
        return
      }

      const data = await res.json()
      if (data.status === 'confirmed') {
        if (pollInterval) clearInterval(pollInterval)
        set({
          qrStatus: 'confirmed',
          scannedUser: data.user,
          availableChannels: data.channels || [],
          defaultChannelId: data.default_channel_id || null,
        })
      } else if (data.status === '2fa_required') {
        if (pollInterval) clearInterval(pollInterval)
        set({ qrStatus: '2fa_required' })
      } else if (data.status === 'expired') {
        if (pollInterval) clearInterval(pollInterval)
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
      if (pollInterval) clearInterval(pollInterval)
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
      set({
        isAuthenticated: true,
        authMode: 'bot',
        channelId: data.channel_id,
        user: { display_name: displayName || 'Storage Bot', first_name: 'Bot' },
        isLoading: false,
      })
    } catch (err: any) {
      set({ isLoading: false, error: err.message })
      throw err
    }
  },

  loginWithDeveloper: async (apiId, apiHash, botToken, channelId, displayName) => {
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
      set({
        isAuthenticated: true,
        authMode: 'developer',
        channelId: data.channel_id,
        user: { display_name: displayName || 'Developer Node', first_name: 'Custom MTProto' },
        isLoading: false,
      })
    } catch (err: any) {
      set({ isLoading: false, error: err.message })
      throw err
    }
  },

  logout: async () => {
    if (pollInterval) clearInterval(pollInterval)
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
      qrStatus: 'idle',
      scannedUser: null,
      isLoading: false,
    })
  },

  clearError: () => set({ error: null }),
}))
