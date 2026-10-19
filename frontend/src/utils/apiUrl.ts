/**
 * Dynamic API Base URL configuration.
 * Resolves to http://127.0.0.1:8000 when running inside Tauri desktop environment,
 * or relative /api/v1 in standard browser / Vite development mode.
 */

export const getBackendOrigin = (): string => {
  if (typeof window !== 'undefined') {
    const isTauri = Boolean(
      (window as any).__TAURI_INTERNALS__ ||
      (window as any).__TAURI__ ||
      window.location.protocol === 'tauri:' ||
      window.location.hostname === 'tauri.localhost'
    )
    if (isTauri) {
      return 'http://127.0.0.1:8000'
    }
  }
  return ''
}

export const BACKEND_ORIGIN = getBackendOrigin()
export const API_BASE = `${BACKEND_ORIGIN}/api/v1`

export const apiUrl = (path: string): string => {
  const cleanPath = path.startsWith('/') ? path : `/${path}`
  return `${BACKEND_ORIGIN}${cleanPath}`
}

export const resolveMediaUrl = (url?: string | null): string => {
  if (!url) return ''
  if (url.startsWith('http://') || url.startsWith('https://') || url.startsWith('blob:') || url.startsWith('data:')) {
    return url
  }
  return `${BACKEND_ORIGIN}${url.startsWith('/') ? url : `/${url}`}`
}
