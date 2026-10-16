import { FileItem, AlbumItem, ShareLinkItem, StorageMetrics } from '../types/vault'

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

  async batchPurge(file_ids: string[]): Promise<{ count: number }> {
    const res = await fetch(`${API_BASE}/files/batch-purge`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ file_ids }),
    })
    if (!res.ok) throw new Error(`Failed to batch purge: ${res.statusText}`)
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
}
