import { FileItem, AlbumItem, ShareLinkItem, StorageMetrics, WatchFolder, WatchFolderEvent } from '../types/vault'

const API_BASE = '/api/v1'

export const api = {
  async getMetrics(): Promise<StorageMetrics> {
    const res = await fetch(`${API_BASE}/system/metrics`)
    if (!res.ok) throw new Error(`Failed to fetch metrics: ${res.statusText}`)
    return res.json()
  },

  async listFiles(params?: {
    category?: string
    tag?: string
    album_id?: string
    q?: string
    is_deleted?: boolean
    limit?: number
    offset?: number
  }): Promise<FileItem[]> {
    const query = new URLSearchParams()
    if (params?.category && params.category !== 'all') query.set('category', params.category)
    if (params?.tag) query.set('tag', params.tag)
    if (params?.album_id) query.set('album_id', params.album_id)
    if (params?.q) query.set('q', params.q)
    if (params?.is_deleted !== undefined) query.set('is_deleted', String(params.is_deleted))
    if (params?.limit) query.set('limit', String(params.limit))
    if (params?.offset) query.set('offset', String(params.offset))

    const res = await fetch(`${API_BASE}/files?${query.toString()}`)
    if (!res.ok) throw new Error(`Failed to list files: ${res.statusText}`)
    return res.json()
  },

  async deleteFile(id: string, purge = false): Promise<{ success: boolean }> {
    const res = await fetch(`${API_BASE}/files/${id}?purge=${purge}`, { method: 'DELETE' })
    if (!res.ok) throw new Error(`Failed to delete file: ${res.statusText}`)
    return res.json()
  },

  async restoreFile(id: string): Promise<{ success: boolean }> {
    const res = await fetch(`${API_BASE}/files/${id}/restore`, { method: 'POST' })
    if (!res.ok) throw new Error(`Failed to restore file: ${res.statusText}`)
    return res.json()
  },

  async batchTrash(file_ids: string[]): Promise<{ count: number }> {
    const res = await fetch(`${API_BASE}/files/batch-trash`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ file_ids }),
    })
    if (!res.ok) throw new Error(`Failed to batch trash: ${res.statusText}`)
    return res.json()
  },

  async batchRestore(file_ids: string[]): Promise<{ count: number }> {
    const res = await fetch(`${API_BASE}/files/batch-restore`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ file_ids }),
    })
    if (!res.ok) throw new Error(`Failed to batch restore: ${res.statusText}`)
    return res.json()
  },

  async batchPurge(file_ids: string[]): Promise<{ count: number; messages_purged: number }> {
    const res = await fetch(`${API_BASE}/files/batch-purge`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ file_ids }),
    })
    if (!res.ok) throw new Error(`Failed to batch purge: ${res.statusText}`)
    return res.json()
  },

  async emptyTrash(): Promise<{ count: number; messages_purged: number }> {
    const res = await fetch(`${API_BASE}/files/empty-trash`, {
      method: 'POST',
    })
    if (!res.ok) throw new Error(`Failed to empty trash: ${res.statusText}`)
    return res.json()
  },

  async listAlbums(): Promise<AlbumItem[]> {
    const res = await fetch(`${API_BASE}/albums`)
    if (!res.ok) throw new Error(`Failed to list albums: ${res.statusText}`)
    return res.json()
  },

  async createAlbum(name: string, description?: string): Promise<AlbumItem> {
    const res = await fetch(`${API_BASE}/albums`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ name, description }),
    })
    if (!res.ok) throw new Error(`Failed to create album: ${res.statusText}`)
    return res.json()
  },

  async deleteAlbum(id: string): Promise<{ success: boolean }> {
    const res = await fetch(`${API_BASE}/albums/${id}`, { method: 'DELETE' })
    if (!res.ok) throw new Error(`Failed to delete album: ${res.statusText}`)
    return res.json()
  },

  async addFileToAlbum(albumId: string, fileId: string): Promise<{ success: boolean }> {
    const res = await fetch(`${API_BASE}/albums/${albumId}/files`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ file_id: fileId }),
    })
    if (!res.ok) throw new Error(`Failed to add file to album: ${res.statusText}`)
    return res.json()
  },

  async listShares(): Promise<ShareLinkItem[]> {
    const res = await fetch(`${API_BASE}/shares`)
    if (!res.ok) throw new Error(`Failed to list shares: ${res.statusText}`)
    return res.json()
  },

  async createShare(file_id: string, hours = 72, has_password = false, password?: string): Promise<ShareLinkItem> {
    const res = await fetch(`${API_BASE}/shares`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ file_id, hours, has_password, password }),
    })
    if (!res.ok) throw new Error(`Failed to create share: ${res.statusText}`)
    return res.json()
  },

  async revokeShare(shareId: string): Promise<{ success: boolean }> {
    const res = await fetch(`${API_BASE}/shares/${shareId}/revoke`, { method: 'POST' })
    if (!res.ok) throw new Error(`Failed to revoke share: ${res.statusText}`)
    return res.json()
  },

  async rebuildIndex(): Promise<{ status: string; restored_envelopes: number }> {
    const res = await fetch(`${API_BASE}/system/rebuild-index`, { method: 'POST' })
    if (!res.ok) throw new Error(`Failed to rebuild index: ${res.statusText}`)
    return res.json()
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
    const res = await fetch(`${API_BASE}/files/${fileId}/upload-status`)
    if (!res.ok) throw new Error(`Failed to fetch upload status: ${res.statusText}`)
    return res.json()
  },

  async cleanupIncompleteUpload(fileId: string): Promise<{ success: boolean; purged_parts: number }> {
    const res = await fetch(`${API_BASE}/files/${fileId}/incomplete`, { method: 'DELETE' })
    if (!res.ok) throw new Error(`Failed to cleanup incomplete upload: ${res.statusText}`)
    return res.json()
  },

  async syncCatalog(): Promise<{ status: string; synced: boolean }> {
    const res = await fetch(`${API_BASE}/catalog/sync`, { method: 'POST' })
    if (!res.ok) throw new Error(`Failed to sync catalog: ${res.statusText}`)
    return res.json()
  },

  // --- Media Workstation Watch Folders ---
  async listWatchFolders(): Promise<WatchFolder[]> {
    const res = await fetch(`${API_BASE}/workstation/watch-folders`)
    if (!res.ok) throw new Error(`Failed to list watch folders: ${res.statusText}`)
    return res.json()
  },

  async createWatchFolder(data: {
    path: string
    label?: string
    target_album_id?: string | null
    auto_delete_local?: boolean
  }): Promise<WatchFolder> {
    const res = await fetch(`${API_BASE}/workstation/watch-folders`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(data),
    })
    if (!res.ok) {
      const err = await res.json().catch(() => ({ detail: res.statusText }))
      throw new Error(err.detail || 'Failed to register watch folder')
    }
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
    const res = await fetch(`${API_BASE}/workstation/watch-folders/${id}`, {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(data),
    })
    if (!res.ok) throw new Error(`Failed to update watch folder: ${res.statusText}`)
    return res.json()
  },

  async deleteWatchFolder(id: string): Promise<{ status: string; id: string }> {
    const res = await fetch(`${API_BASE}/workstation/watch-folders/${id}`, { method: 'DELETE' })
    if (!res.ok) throw new Error(`Failed to delete watch folder: ${res.statusText}`)
    return res.json()
  },

  async scanWatchFolder(id: string): Promise<{ status: string; folder_id: string; queued_files: number }> {
    const res = await fetch(`${API_BASE}/workstation/watch-folders/${id}/scan`, { method: 'POST' })
    if (!res.ok) {
      const err = await res.json().catch(() => ({ detail: res.statusText }))
      throw new Error(err.detail || 'Failed to scan watch folder')
    }
    return res.json()
  },

  async listWatchEvents(folder_id?: string, limit = 50): Promise<WatchFolderEvent[]> {
    const url = folder_id
      ? `${API_BASE}/workstation/events?folder_id=${encodeURIComponent(folder_id)}&limit=${limit}`
      : `${API_BASE}/workstation/events?limit=${limit}`
    const res = await fetch(url)
    if (!res.ok) throw new Error(`Failed to list watch events: ${res.statusText}`)
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
  }> {
    const res = await fetch(`${API_BASE}/vault/channel-health`)
    if (!res.ok) throw new Error(`Failed to fetch channel health: ${res.statusText}`)
    return res.json()
  },

  async setBackupChannel(backupChannelId: string): Promise<{ status: string; backup_channel_id: string }> {
    const res = await fetch(`${API_BASE}/vault/backup-channel`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ backup_channel_id: backupChannelId })
    })
    if (!res.ok) {
      const err = await res.json().catch(() => ({ detail: res.statusText }))
      throw new Error(err.detail || 'Failed to configure backup channel')
    }
    return res.json()
  },

  exportCatalogUrl(): string {
    return `${API_BASE}/vault/catalog/export`
  },

  async importCatalog(file: File): Promise<{ status: string; restored_files: number; total_catalog_files: number }> {
    const formData = new FormData()
    formData.append('catalog_file', file)
    const res = await fetch(`${API_BASE}/vault/catalog/import`, {
      method: 'POST',
      body: formData
    })
    if (!res.ok) {
      const err = await res.json().catch(() => ({ detail: res.statusText }))
      throw new Error(err.detail || 'Failed to import vault catalog')
    }
    return res.json()
  }
}


