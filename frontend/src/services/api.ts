import { FileItem, AlbumItem, StorageMetrics, WatchFolder, WatchFolderEvent, LicenseStatus } from '../types/vault'
import { useAuthStore } from '../store/useAuthStore'

export { getBackendOrigin, BACKEND_ORIGIN, API_BASE, apiUrl, resolveMediaUrl } from '../utils/apiUrl'
import { API_BASE } from '../utils/apiUrl'

// H-11: Central fetch wrapper that intercepts 401 and notifies useAuthStore of session expiration
async function authFetch(input: RequestInfo | URL, init?: RequestInit): Promise<Response> {
  const res = await fetch(input, init)
  if (res.status === 401) {
    useAuthStore.getState().handleSessionExpired()
  }
  return res
}

// H-23: Surface the backend error `detail` (FloodWait, validation, path-security, ...)
// instead of a generic statusText, so actionable messages reach the user.
function extractErrorMessage(body: string | null | undefined, fallback: string): string {
  if (!body) return fallback
  try {
    const data = JSON.parse(body)
    const detail = data?.detail ?? data?.message
    if (typeof detail === 'string' && detail) return detail
    if (Array.isArray(detail)) {
      // FastAPI validation errors: [{loc, msg, type}, ...]
      const msgs = detail
        .map((d: any) => (typeof d === 'string' ? d : d?.msg))
        .filter(Boolean)
      if (msgs.length > 0) return msgs.join('; ')
    } else if (detail && typeof detail === 'object') {
      return JSON.stringify(detail)
    }
    return fallback
  } catch {
    // Non-JSON body (e.g. HTML error page): return a bounded snippet
    return body.slice(0, 200)
  }
}

async function parseError(res: Response, fallback: string): Promise<Error> {
  let body: string | null = null
  try {
    body = await res.text()
  } catch {}
  const fallbackWithStatus = res.statusText ? `${fallback}: ${res.statusText}` : fallback
  return new Error(extractErrorMessage(body, fallbackWithStatus))
}

export const api = {
  async getMetrics(): Promise<StorageMetrics> {
    const res = await authFetch(`${API_BASE}/system/metrics`)
    if (!res.ok) throw await parseError(res, 'Failed to fetch metrics')
    return res.json()
  },

  async listFiles(
    params?: {
      category?: string
      tag?: string
      album_id?: string
      q?: string
      is_deleted?: boolean
      limit?: number
      offset?: number
    },
    options?: { signal?: AbortSignal }
  ): Promise<FileItem[]> {
    const query = new URLSearchParams()
    if (params?.category && params.category !== 'all') query.set('category', params.category)
    if (params?.tag) query.set('tag', params.tag)
    if (params?.album_id) query.set('album_id', params.album_id)
    if (params?.q) query.set('q', params.q)
    if (params?.is_deleted !== undefined) query.set('is_deleted', String(params.is_deleted))
    if (params?.limit) query.set('limit', String(params.limit))
    if (params?.offset) query.set('offset', String(params.offset))

    const res = await authFetch(`${API_BASE}/files?${query.toString()}`, { signal: options?.signal })
    if (!res.ok) throw await parseError(res, 'Failed to list files')
    return res.json()
  },

  async restoreFile(id: string): Promise<{ success: boolean }> {
    const res = await authFetch(`${API_BASE}/files/${id}/restore`, { method: 'POST' })
    if (!res.ok) throw await parseError(res, 'Failed to restore file')
    return res.json()
  },

  async batchTrash(file_ids: string[]): Promise<{ count: number }> {
    const res = await authFetch(`${API_BASE}/files/batch-trash`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ file_ids }),
    })
    if (!res.ok) throw await parseError(res, 'Failed to batch trash')
    return res.json()
  },

  async batchRestore(file_ids: string[]): Promise<{ count: number }> {
    const res = await authFetch(`${API_BASE}/files/batch-restore`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ file_ids }),
    })
    if (!res.ok) throw await parseError(res, 'Failed to batch restore')
    return res.json()
  },

  async batchPurge(file_ids: string[]): Promise<{ count: number; messages_purged: number }> {
    const res = await authFetch(`${API_BASE}/files/batch-purge`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ file_ids }),
    })
    if (!res.ok) throw await parseError(res, 'Failed to batch purge')
    return res.json()
  },

  async emptyTrash(): Promise<{ count: number; messages_purged: number }> {
    const res = await authFetch(`${API_BASE}/files/empty-trash`, {
      method: 'POST',
    })
    if (!res.ok) throw await parseError(res, 'Failed to empty trash')
    return res.json()
  },

  async listAlbums(): Promise<AlbumItem[]> {
    const res = await authFetch(`${API_BASE}/albums`)
    if (!res.ok) throw await parseError(res, 'Failed to list albums')
    return res.json()
  },

  async createAlbum(name: string, description?: string): Promise<AlbumItem> {
    const res = await authFetch(`${API_BASE}/albums`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ name, description }),
    })
    if (!res.ok) throw await parseError(res, 'Failed to create album')
    return res.json()
  },

  async addFileToAlbum(albumId: string, fileId: string): Promise<{ success: boolean }> {
    const res = await authFetch(`${API_BASE}/albums/${albumId}/files`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ file_id: fileId }),
    })
    if (!res.ok) throw await parseError(res, 'Failed to add file to album')
    return res.json()
  },

  async rebuildIndex(): Promise<{ status: string; restored_envelopes: number }> {
    const res = await authFetch(`${API_BASE}/system/rebuild-index`, { method: 'POST' })
    if (!res.ok) throw await parseError(res, 'Failed to rebuild index')
    return res.json()
  },

  async initUpload(params: {
    filename: string
    total_size: number
    mime_type?: string
    total_parts?: number
    nonce?: string
    encryption?: string
  }): Promise<{
    file_id: string
    filename: string
    total_parts: number
    upload_status: string
    nonce?: string
    encryption?: string
  }> {
    const form = new FormData()
    form.append('filename', params.filename)
    form.append('total_size', String(params.total_size))
    if (params.mime_type) form.append('mime_type', params.mime_type)
    if (params.total_parts) form.append('total_parts', String(params.total_parts))
    if (params.nonce) form.append('nonce', params.nonce)
    if (params.encryption) form.append('encryption', params.encryption)

    const res = await authFetch(`${API_BASE}/upload/init`, {
      method: 'POST',
      body: form,
    })
    if (!res.ok) throw await parseError(res, 'Failed to initialize upload')
    return res.json()
  },

  uploadChunk(
    formData: FormData,
    onProgress?: (loaded: number, total: number) => void
  ): { xhr: XMLHttpRequest; promise: Promise<{ file_id: string; part_index: number; uploaded_parts: number; total_parts: number; is_completed: boolean; status: string }> } {
    const xhr = new XMLHttpRequest()
    const promise = new Promise<any>((resolve, reject) => {
      if (onProgress) {
        xhr.upload.onprogress = (e) => {
          if (e.lengthComputable) {
            onProgress(e.loaded, e.total)
          }
        }
      }
      xhr.onload = () => {
        if (xhr.status === 401) {
          useAuthStore.getState().handleSessionExpired()
        }
        if (xhr.status >= 200 && xhr.status < 300) {
          try {
            resolve(JSON.parse(xhr.responseText))
          } catch {
            resolve({ status: 'completed' })
          }
        } else {
          reject(new Error(extractErrorMessage(xhr.responseText, `Chunk upload failed with status ${xhr.status}`)))
        }
      }
      xhr.onerror = () => reject(new Error('Network error during chunk upload'))
      xhr.onabort = () => reject(new Error('Chunk upload aborted'))

      xhr.open('POST', `${API_BASE}/upload/chunk`)
      xhr.send(formData)
    })
    return { xhr, promise }
  },

  async getUploadStatus(fileId: string): Promise<{
    file_id: string
    filename: string
    total_parts: number
    uploaded_parts: number[]
    uploaded_count: number
    missing_parts: number[]
    next_part: number | null
    is_interrupted: boolean
    upload_status: string
    nonce?: string
  }> {
    const res = await authFetch(`${API_BASE}/files/${fileId}/upload-status`)
    if (!res.ok) throw await parseError(res, 'Failed to fetch upload status')
    return res.json()
  },

  async cleanupIncompleteUpload(fileId: string): Promise<{ success: boolean; purged_parts: number }> {
    const res = await authFetch(`${API_BASE}/files/${fileId}/incomplete`, { method: 'DELETE' })
    if (!res.ok) throw await parseError(res, 'Failed to cleanup incomplete upload')
    return res.json()
  },

  // --- Media Workstation Watch Folders ---
  async listWatchFolders(): Promise<WatchFolder[]> {
    const res = await authFetch(`${API_BASE}/workstation/watch-folders`)
    if (!res.ok) throw await parseError(res, 'Failed to list watch folders')
    return res.json()
  },

  async createWatchFolder(data: {
    path: string
    label?: string
    target_album_id?: string | null
    auto_delete_local?: boolean
  }): Promise<WatchFolder> {
    const res = await authFetch(`${API_BASE}/workstation/watch-folders`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(data),
    })
    if (!res.ok) throw await parseError(res, 'Failed to register watch folder')
    return res.json()
  },

  async updateWatchFolder(
    id: string,
    data: {
      label?: string
      target_album_id?: string | null
      auto_delete_local?: boolean
      is_active?: boolean
    }
  ): Promise<{ status: string; id: string }> {
    const res = await authFetch(`${API_BASE}/workstation/watch-folders/${id}`, {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(data),
    })
    if (!res.ok) throw await parseError(res, 'Failed to update watch folder')
    return res.json()
  },

  async deleteWatchFolder(id: string): Promise<{ status: string; id: string }> {
    const res = await authFetch(`${API_BASE}/workstation/watch-folders/${id}`, { method: 'DELETE' })
    if (!res.ok) throw await parseError(res, 'Failed to delete watch folder')
    return res.json()
  },

  async scanWatchFolder(id: string): Promise<{ status: string; folder_id: string; queued_files: number }> {
    const res = await authFetch(`${API_BASE}/workstation/watch-folders/${id}/scan`, { method: 'POST' })
    if (!res.ok) throw await parseError(res, 'Failed to scan watch folder')
    return res.json()
  },

  async listWatchEvents(folder_id?: string, limit = 50): Promise<WatchFolderEvent[]> {
    const url = folder_id
      ? `${API_BASE}/workstation/events?folder_id=${encodeURIComponent(folder_id)}&limit=${limit}`
      : `${API_BASE}/workstation/events?limit=${limit}`
    const res = await authFetch(url)
    if (!res.ok) throw await parseError(res, 'Failed to list watch events')
    return res.json()
  },

  // --- Vault Redundancy & Disaster Recovery ---
  async getChannelHealth(): Promise<{
    online: boolean
    flood_wait_seconds: number
    max_concurrent_workers: number
    primary_channel: {
      id: string
      healthy: boolean
      title?: string | null
      can_post?: boolean
      error?: string | null
    }
    backup_channel: {
      id?: string | null
      configured: boolean
      healthy: boolean
      error?: string | null
    }
    saved_messages_mirror: boolean
    local_backup: {
      exists: boolean
      path: string
    }
    catalog_sync?: {
      status: 'idle' | 'syncing' | 'synced' | 'failed'
      last_synced_at?: string | null
      last_error?: string | null
      pending?: boolean
      retry_count?: number
      pinned_msg_id?: number | null
    }
  }> {
    const res = await authFetch(`${API_BASE}/vault/channel-health`)
    if (!res.ok) throw await parseError(res, 'Failed to fetch channel health')
    return res.json()
  },

  async setBackupChannel(backupChannelId: string): Promise<{ status: string; backup_channel_id: string }> {
    const res = await authFetch(`${API_BASE}/vault/backup-channel`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ backup_channel_id: backupChannelId })
    })
    if (!res.ok) throw await parseError(res, 'Failed to configure backup channel')
    return res.json()
  },

  exportCatalogUrl(): string {
    return `${API_BASE}/vault/catalog/export`
  },

  async importCatalog(file: File): Promise<{ status: string; restored_files: number; total_catalog_files: number }> {
    const formData = new FormData()
    formData.append('catalog_file', file)
    const res = await authFetch(`${API_BASE}/vault/catalog/import`, {
      method: 'POST',
      body: formData
    })
    if (!res.ok) throw await parseError(res, 'Failed to import vault catalog')
    return res.json()
  },

  async getVaultStatus(): Promise<{ unlocked: boolean; encryption_enabled: boolean }> {
    const res = await authFetch(`${API_BASE}/vault/status`)
    if (!res.ok) throw await parseError(res, 'Failed to fetch vault status')
    return res.json()
  },

  async unlockVault(payload: { passphrase?: string; key?: string }): Promise<{ unlocked: boolean; message: string }> {
    const res = await authFetch(`${API_BASE}/vault/unlock`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload)
    })
    if (!res.ok) throw await parseError(res, 'Failed to unlock vault')
    return res.json()
  },

  async lockVault(): Promise<{ unlocked: boolean; message: string }> {
    const res = await authFetch(`${API_BASE}/vault/lock`, {
      method: 'POST'
    })
    if (!res.ok) throw await parseError(res, 'Failed to lock vault')
    return res.json()
  },

  async getLicenseStatus(): Promise<LicenseStatus> {
    const res = await authFetch(`${API_BASE}/license`)
    if (!res.ok) throw await parseError(res, 'Failed to fetch license status')
    return res.json()
  },

  async activateLicense(license_key: string): Promise<{ status: string; license: LicenseStatus }> {
    const res = await authFetch(`${API_BASE}/license/activate`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ license_key })
    })
    if (!res.ok) throw await parseError(res, 'Failed to activate Pro license')
    return res.json()
  },

  async getNasHealth(): Promise<any> {
    const res = await authFetch(`${API_BASE}/nas/health`)
    if (!res.ok) throw await parseError(res, 'Failed to check NAS health')
    return res.json()
  },

  async syncNasVault(): Promise<any> {
    const res = await authFetch(`${API_BASE}/nas/sync`, { method: 'POST' })
    if (!res.ok) throw await parseError(res, 'Failed to sync NAS vault')
    return res.json()
  },

  async getS3Health(): Promise<any> {
    const res = await authFetch(`${API_BASE}/storage/s3/health`)
    if (!res.ok) throw await parseError(res, 'Failed to check S3 health')
    return res.json()
  },

  async getNextcloudHealth(): Promise<any> {
    const res = await authFetch(`${API_BASE}/storage/nextcloud/health`)
    if (!res.ok) throw await parseError(res, 'Failed to check Nextcloud health')
    return res.json()
  },

  async getStorageConfigs(): Promise<any> {
    const res = await authFetch(`${API_BASE}/storage/config`)
    if (!res.ok) throw await parseError(res, 'Failed to fetch storage configs')
    return res.json()
  },

  async saveS3Config(config: {
    endpoint_url: string
    bucket_name: string
    access_key_id: string
    secret_access_key?: string
    region_name?: string
    vault_prefix?: string
  }): Promise<any> {
    const res = await authFetch(`${API_BASE}/storage/s3/config`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(config)
    })
    if (!res.ok) throw await parseError(res, 'Failed to save S3 configuration')
    return res.json()
  },

  async saveNextcloudConfig(config: {
    webdav_url: string
    username: string
    app_password?: string
    vault_prefix?: string
  }): Promise<any> {
    const res = await authFetch(`${API_BASE}/storage/nextcloud/config`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(config)
    })
    if (!res.ok) throw await parseError(res, 'Failed to save Nextcloud configuration')
    return res.json()
  }
}


