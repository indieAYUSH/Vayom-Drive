import React from 'react'
import { render, screen, fireEvent } from '@testing-library/react'
import { describe, it, expect, beforeEach, vi } from 'vitest'
import '@testing-library/jest-dom/vitest'
import { LoginView } from '../LoginView'
import { useAuthStore } from '../../store/useAuthStore'

describe('LoginView Component State Machine', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    useAuthStore.setState({
      isAuthenticated: false,
      isInitializing: false,
      isLoading: false,
      authStep: 'choosing',
      qrMode: 'own',
      hasSavedKeys: false,
      savedApiId: null,
      savedApiHashMasked: null,
      devModeEnabled: false,
      qrUrl: null,
      qrStatus: 'idle',
      scannedUser: null,
      availableChannels: [],
      error: null,
    })
  })

  it('renders choosing screen with single-column layout and correct headings', () => {
    render(<LoginView />)

    expect(screen.getByText('Connect your storage')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /Sign in with Telegram/i })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /Connect a NAS/i })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /Other ways to sign in/i })).toBeInTheDocument()

    // Footer is strictly "Vayom Drive v1.0.0"
    expect(screen.getByText('Vayom Drive v1.0.0')).toBeInTheDocument()
  })

  it('transitions to needs_keys when Telegram is clicked without saved keys', () => {
    render(<LoginView />)

    fireEvent.click(screen.getByRole('button', { name: /Sign in with Telegram/i }))

    expect(screen.getByText('Add your Telegram API keys')).toBeInTheDocument()
    expect(screen.getByText(/Telegram needs an API ID and hash for this app/i)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /Get keys automatically/i })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /I already have keys/i })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /Skip and use shared keys/i })).toBeInTheDocument()
  })

  it('reveals manual entry fields when "I already have keys" is clicked', () => {
    useAuthStore.setState({ authStep: 'needs_keys' })
    render(<LoginView />)

    fireEvent.click(screen.getByRole('button', { name: /I already have keys/i }))

    expect(screen.getByText('API ID')).toBeInTheDocument()
    expect(screen.getByText('API Hash')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /Continue/i })).toBeInTheDocument()
  })

  it('transitions to auto_phone step from needs_keys', () => {
    useAuthStore.setState({ authStep: 'needs_keys' })
    render(<LoginView />)

    fireEvent.click(screen.getByRole('button', { name: /Get keys automatically/i }))

    expect(screen.getByText('Enter your phone number')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /Send login code/i })).toBeInTheDocument()
  })

  it('renders auto_code step with required helper text', () => {
    useAuthStore.setState({ authStep: 'auto_code' })
    render(<LoginView />)

    expect(screen.getByText('Enter confirmation code')).toBeInTheDocument()
    expect(
      screen.getByText(/Telegram will send a login code to your Telegram app\. The code signs in to my\.telegram\.org/i)
    ).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /Confirm and get keys/i })).toBeInTheDocument()
  })

  it('renders qr_waiting screen with hero QR and numbered steps', () => {
    useAuthStore.setState({
      authStep: 'qr_waiting',
      qrMode: 'own',
      savedApiId: 123456,
      qrUrl: 'tg://login?token=test_token_123',
      qrStatus: 'waiting',
    })
    render(<LoginView />)

    expect(screen.getByText('Sign in with Telegram')).toBeInTheDocument()
    expect(screen.getByText('Open Telegram on your phone')).toBeInTheDocument()
    expect(screen.getByText('Go to Settings > Devices')).toBeInTheDocument()
    expect(screen.getByText('Tap Link Desktop Device')).toBeInTheDocument()
    expect(screen.getByText('Scan this code')).toBeInTheDocument()
    expect(screen.getByText(/Using your own API keys/i)).toBeInTheDocument()

    // No countdown text anywhere
    expect(screen.queryByText(/Expires in/i)).not.toBeInTheDocument()
  })

  it('renders shared keys warning honestly in qr_waiting mode', () => {
    useAuthStore.setState({
      authStep: 'qr_waiting',
      qrMode: 'shared',
      qrUrl: 'tg://login?token=shared_token_xyz',
      qrStatus: 'waiting',
    })
    render(<LoginView />)

    expect(screen.getByText(/Using shared keys/i)).toBeInTheDocument()
    expect(screen.getByText('Shared keys can hit Telegram rate limits sooner.')).toBeInTheDocument()
  })

  it('renders 2FA screen when status is needs_2fa', () => {
    useAuthStore.setState({
      authStep: 'needs_2fa',
      qrStatus: '2fa_required',
    })
    render(<LoginView />)

    expect(screen.getByText('Enter your Telegram password')).toBeInTheDocument()
    expect(screen.getByText('Your account has two-step verification.')).toBeInTheDocument()
    expect(screen.getByPlaceholderText('Two-step verification password')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /Submit password/i })).toBeInTheDocument()
  })

  it('renders NAS form with test connection and connect button', () => {
    useAuthStore.setState({ authStep: 'nas_form' })
    render(<LoginView />)

    expect(screen.getByText('Connect a NAS')).toBeInTheDocument()
    expect(screen.getByText('NAS vault root path')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /Test connection/i })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /Connect NAS/i })).toBeInTheDocument()
  })

  it('never displays deleted/forbidden strings in any state', () => {
    const states = ['choosing', 'needs_keys', 'auto_phone', 'auto_code', 'qr_waiting', 'needs_2fa', 'nas_form'] as const
    const forbidden = [
      'Zero Ban Risk Guarantee',
      'Sovereign',
      'Zero-knowledge',
      'Authentication Required',
      'NODE GATEWAY',
      'STANDALONE CLIENT',
      'PRIMARY // ANTI-BAN',
    ]

    for (const step of states) {
      useAuthStore.setState({ authStep: step, qrUrl: 'tg://mock' })
      const { container } = render(<LoginView />)
      for (const phrase of forbidden) {
        expect(container.textContent).not.toContain(phrase)
      }
    }
  })
})
