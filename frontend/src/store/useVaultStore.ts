import { create } from 'zustand'
import { FileItem, AlbumItem, ShareLinkItem, StorageMetrics, FileCategory } from '../types/vault'
import { api } from '../services/api'

interface VaultState {
  // Data
  files: FileItem[]
  albums: AlbumItem[]
  shares: ShareLinkItem[]
  metrics: StorageMetrics
  isLoading: boolean
  error: string | null

  // Navigation & Filtering
  activeTab: 'all' | 'images' | 'videos' | 'screenshots' | 'documents' | 'archives' | 'audio' | 'starred' | 'folders' | 'albums' | 'trash' | 'shares' | 'settings'
  primaryMode: 'photos' | 'drive'
  selectedAlbumId: string | null
  selectedTag: string | null
  searchQuery: string
  activeCategoryFilter: FileCategory | 'all'
  timelineGroupByDate: boolean
  driveViewMode: 'grid' | 'table'

  // Starred / Favorites & Audio Player
  starredIds: string[]
  activeAudioItem: FileItem | null

  // Selection
  selectedIds: string[]

  // Modals & Overlays
  lightboxItem: FileItem | null
  isCommandBarOpen: boolean
  isUploadModalOpen: boolean
  isCreateAlbumModalOpen: boolean
  isShareModalOpen: boolean
  shareTargetFile: FileItem | null

  // Data Actions
  fetchFiles: () => Promise<void>
  fetchMetrics: () => Promise<void>
  fetchAlbums: () => Promise<void>
  fetchShares: () => Promise<void>
  init: () => Promise<void>

  // Navigation Actions
  setActiveTab: (tab: VaultState['activeTab']) => void
  setPrimaryMode: (mode: 'photos' | 'drive') => void
  setSelectedAlbumId: (id: string | null) => void
  setSelectedTag: (tag: string | null) => void
  setSearchQuery: (query: string) => void
  setCategoryFilter: (category: FileCategory | 'all') => void
  setTimelineGroupByDate: (group: boolean) => void
  setDriveViewMode: (mode: 'grid' | 'table') => void

  toggleStar: (id: string) => void
  setActiveAudioItem: (item: FileItem | null) => void

  toggleSelect: (id: string) => void
  selectAll: () => void
  clearSelection: () => void

  openLightbox: (item: FileItem) => void
  closeLightbox: () => void
  nextLightbox: () => void
  prevLightbox: () => void

  setCommandBarOpen: (open: boolean) => void
  setUploadModalOpen: (open: boolean) => void
  setCreateAlbumModalOpen: (open: boolean) => void
  openShareModal: (file: FileItem) => void
  closeShareModal: () => void

  // File mutations
  moveToTrash: (ids: string[]) => Promise<void>
  restoreFromTrash: (ids: string[]) => Promise<void>
  purgeFiles: (ids: string[]) => Promise<void>
  emptyTrash: () => Promise<void>
  createAlbum: (name: string, description?: string) => Promise<void>
  createShareLink: (fileId: string, hours: number, hasPassword?: boolean, password?: string) => Promise<void>
  revokeShareLink: (shareId: string) => Promise<void>
  rebuildIndex: () => Promise<{ status: string; restored_envelopes: number }>
}

const getInitialStarred = (): string[] => {
  try {
    const raw = localStorage.getItem('televault_starred')
    return raw ? JSON.parse(raw) : []
  } catch {
    return []
  }
}

export const useVaultStore = create<VaultState>((set, get) => ({
  files: [],
  albums: [],
  shares: [],
  metrics: {
    total_bytes: 0,
    total_files: 0,
    channel_id: 'Loading...',
    telegram_messages: 0,
    online_status: 'online',
  },
  isLoading: false,
  error: null,

  activeTab: 'all',
  primaryMode: 'photos',
  selectedAlbumId: null,
  selectedTag: null,
  searchQuery: '',
  activeCategoryFilter: 'all',
  timelineGroupByDate: true,
  driveViewMode: 'grid',

  starredIds: getInitialStarred(),
  activeAudioItem: null,

  selectedIds: [],
  lightboxItem: null,
  isCommandBarOpen: false,
  isUploadModalOpen: false,
  isCreateAlbumModalOpen: false,
  isShareModalOpen: false,
  shareTargetFile: null,

  setPrimaryMode: (mode) => set({ primaryMode: mode }),
  setTimelineGroupByDate: (group) => set({ timelineGroupByDate: group }),
  setDriveViewMode: (mode) => set({ driveViewMode: mode }),
  toggleStar: (id) =>
    set((state) => {
      const exists = state.starredIds.includes(id)
      const updated = exists ? state.starredIds.filter((item) => item !== id) : [...state.starredIds, id]
      try {
        localStorage.setItem('televault_starred', JSON.stringify(updated))
      } catch {}
      return { starredIds: updated }
    }),
  setActiveAudioItem: (item) => set({ activeAudioItem: item }),

  fetchFiles: async () => {
    try {
      const { activeTab, selectedAlbumId, selectedTag, searchQuery, activeCategoryFilter } = get()
      const isTrash = activeTab === 'trash'

      let cat: string | undefined = undefined
      if (activeCategoryFilter !== 'all') {
        cat = activeCategoryFilter
      } else if (activeTab === 'images') cat = 'image'
      else if (activeTab === 'videos') cat = 'video'
      else if (activeTab === 'documents') cat = 'document'
      else if (activeTab === 'archives') cat = 'archive'

      const isFolderTab = activeTab === 'folders' || activeTab === 'albums'
      const files = await api.listFiles({
        category: cat,
        tag: selectedTag || undefined,
        album_id: isFolderTab && selectedAlbumId ? selectedAlbumId : undefined,
        q: searchQuery.trim() || undefined,
        is_deleted: isTrash,
        limit: 500,
      })

      set({ files, error: null })
    } catch (err: any) {
      set({ error: err.message || 'Failed to fetch files' })
    }
  },

  fetchMetrics: async () => {
    try {
      const metrics = await api.getMetrics()
      set({ metrics })
    } catch (err) {
      console.error('Failed to fetch storage metrics', err)
    }
  },

  fetchAlbums: async () => {
    try {
      const albums = await api.listAlbums()
      set({ albums })
    } catch (err) {
      console.error('Failed to fetch albums', err)
    }
  },

  fetchShares: async () => {
    try {
      const shares = await api.listShares()
      set({ shares })
    } catch (err) {
      console.error('Failed to fetch shares', err)
    }
  },

  init: async () => {
    set({ isLoading: true })
    await Promise.allSettled([
      get().fetchFiles(),
      get().fetchMetrics(),
      get().fetchAlbums(),
      get().fetchShares(),
    ])
    set({ isLoading: false })
  },

  setActiveTab: (tab) => {
    set({ activeTab: tab, selectedAlbumId: null, selectedTag: null, selectedIds: [] })
    get().fetchFiles()
  },

  setSelectedAlbumId: (id) => {
    set({ selectedAlbumId: id, activeTab: 'folders', selectedIds: [] })
    get().fetchFiles()
  },

  setSelectedTag: (tag) => {
    set({ selectedTag: tag, selectedIds: [] })
    get().fetchFiles()
  },

  setSearchQuery: (query) => {
    set({ searchQuery: query })
    get().fetchFiles()
  },

  setCategoryFilter: (category) => {
    set({ activeCategoryFilter: category })
    get().fetchFiles()
  },

  toggleSelect: (id) => set((state) => {
    const exists = state.selectedIds.includes(id)
    return {
      selectedIds: exists ? state.selectedIds.filter((item) => item !== id) : [...state.selectedIds, id]
    }
  }),

  selectAll: () => {
    const visibleFiles = get().files.filter((f) => !f.is_deleted)
    set({ selectedIds: visibleFiles.map((f) => f.id) })
  },

  clearSelection: () => set({ selectedIds: [] }),

  openLightbox: (item) => set({ lightboxItem: item }),
  closeLightbox: () => set({ lightboxItem: null }),
  nextLightbox: () => {
    const { files, lightboxItem } = get()
    if (!lightboxItem) return
    const activeList = files.filter((f) => !f.is_deleted)
    const currentIndex = activeList.findIndex((f) => f.id === lightboxItem.id)
    if (currentIndex >= 0 && currentIndex < activeList.length - 1) {
      set({ lightboxItem: activeList[currentIndex + 1] })
    }
  },
  prevLightbox: () => {
    const { files, lightboxItem } = get()
    if (!lightboxItem) return
    const activeList = files.filter((f) => !f.is_deleted)
    const currentIndex = activeList.findIndex((f) => f.id === lightboxItem.id)
    if (currentIndex > 0) {
      set({ lightboxItem: activeList[currentIndex - 1] })
    }
  },

  setCommandBarOpen: (open) => set({ isCommandBarOpen: open }),
  setUploadModalOpen: (open) => set({ isUploadModalOpen: open }),
  setCreateAlbumModalOpen: (open) => set({ isCreateAlbumModalOpen: open }),
  openShareModal: (file) => set({ isShareModalOpen: true, shareTargetFile: file }),
  closeShareModal: () => set({ isShareModalOpen: false, shareTargetFile: null }),

  moveToTrash: async (ids) => {
    await api.batchTrash(ids)
    set({ selectedIds: [] })
    await Promise.all([get().fetchFiles(), get().fetchMetrics()])
  },

  restoreFromTrash: async (ids) => {
    await api.batchRestore(ids)
    set({ selectedIds: [] })
    await Promise.all([get().fetchFiles(), get().fetchMetrics()])
  },

  purgeFiles: async (ids) => {
    await api.batchPurge(ids)
    set({ selectedIds: [] })
    await Promise.all([get().fetchFiles(), get().fetchMetrics()])
  },

  emptyTrash: async () => {
    await api.emptyTrash()
    set({ selectedIds: [] })
    await Promise.all([get().fetchFiles(), get().fetchMetrics()])
  },

  createAlbum: async (name, description) => {
    await api.createAlbum(name, description)
    set({ isCreateAlbumModalOpen: false })
    await get().fetchAlbums()
  },

  createShareLink: async (fileId, hours, hasPassword = false, password?: string) => {
    await api.createShare(fileId, hours, hasPassword, password)
    set({ isShareModalOpen: false, shareTargetFile: null })
    await get().fetchShares()
  },

  revokeShareLink: async (shareId) => {
    await api.revokeShare(shareId)
    await get().fetchShares()
  },

  rebuildIndex: async () => {
    const result = await api.rebuildIndex()
    await Promise.all([get().fetchFiles(), get().fetchMetrics(), get().fetchAlbums(), get().fetchShares()])
    return result
  },
}))
