import { create } from 'zustand'
import { FileItem, AlbumItem, ShareLinkItem, StorageMetrics, FileCategory } from '../types/vault'
import { INITIAL_FILES, INITIAL_ALBUMS, INITIAL_SHARES, INITIAL_STORAGE_METRICS } from '../services/mockData'

interface VaultState {
  // Data
  files: FileItem[]
  albums: AlbumItem[]
  shares: ShareLinkItem[]
  metrics: StorageMetrics

  // Navigation & Filtering
  activeTab: 'all' | 'images' | 'videos' | 'documents' | 'archives' | 'albums' | 'trash' | 'shares' | 'settings'
  selectedAlbumId: string | null
  selectedTag: string | null
  searchQuery: string
  activeCategoryFilter: FileCategory | 'all'

  // Selection
  selectedIds: string[]

  // Modals & Overlays
  lightboxItem: FileItem | null
  isCommandBarOpen: boolean
  isUploadModalOpen: boolean
  isCreateAlbumModalOpen: boolean
  isShareModalOpen: boolean
  shareTargetFile: FileItem | null

  // Actions
  setActiveTab: (tab: VaultState['activeTab']) => void
  setSelectedAlbumId: (id: string | null) => void
  setSelectedTag: (tag: string | null) => void
  setSearchQuery: (query: string) => void
  setCategoryFilter: (category: FileCategory | 'all') => void

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
  moveToTrash: (ids: string[]) => void
  restoreFromTrash: (ids: string[]) => void
  purgeFiles: (ids: string[]) => void
  addFiles: (newFiles: FileItem[]) => void
  createAlbum: (name: string, description?: string) => void
  createShareLink: (fileId: string, hours: number, hasPassword?: boolean) => void
  revokeShareLink: (shareId: string) => void
}

export const useVaultStore = create<VaultState>((set, get) => ({
  files: INITIAL_FILES,
  albums: INITIAL_ALBUMS,
  shares: INITIAL_SHARES,
  metrics: INITIAL_STORAGE_METRICS,

  activeTab: 'all',
  selectedAlbumId: null,
  selectedTag: null,
  searchQuery: '',
  activeCategoryFilter: 'all',

  selectedIds: [],
  lightboxItem: null,
  isCommandBarOpen: false,
  isUploadModalOpen: false,
  isCreateAlbumModalOpen: false,
  isShareModalOpen: false,
  shareTargetFile: null,

  setActiveTab: (tab) => set({ activeTab: tab, selectedAlbumId: null, selectedTag: null, selectedIds: [] }),
  setSelectedAlbumId: (id) => set({ selectedAlbumId: id, activeTab: 'albums', selectedIds: [] }),
  setSelectedTag: (tag) => set({ selectedTag: tag, selectedIds: [] }),
  setSearchQuery: (query) => set({ searchQuery: query }),
  setCategoryFilter: (category) => set({ activeCategoryFilter: category }),

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

  moveToTrash: (ids) => set((state) => {
    const now = new Date().toISOString()
    return {
      files: state.files.map((f) => ids.includes(f.id) ? { ...f, is_deleted: true, deleted_at: now } : f),
      selectedIds: []
    }
  }),
  restoreFromTrash: (ids) => set((state) => ({
    files: state.files.map((f) => ids.includes(f.id) ? { ...f, is_deleted: false, deleted_at: undefined } : f),
    selectedIds: []
  })),
  purgeFiles: (ids) => set((state) => ({
    files: state.files.filter((f) => !ids.includes(f.id)),
    selectedIds: []
  })),

  addFiles: (newFiles) => set((state) => ({
    files: [...newFiles, ...state.files],
    metrics: {
      ...state.metrics,
      total_files: state.metrics.total_files + newFiles.length,
      total_bytes: state.metrics.total_bytes + newFiles.reduce((acc, f) => acc + f.size_bytes, 0)
    }
  })),

  createAlbum: (name, description) => set((state) => {
    const newAlbum: AlbumItem = {
      id: `alb-${Date.now()}`,
      name,
      description,
      file_count: 0,
      created_at: new Date().toISOString()
    }
    return {
      albums: [...state.albums, newAlbum],
      isCreateAlbumModalOpen: false
    }
  }),

  createShareLink: (fileId, hours, hasPassword = false) => set((state) => {
    const target = state.files.find((f) => f.id === fileId)
    if (!target) return state
    const expires = new Date(Date.now() + hours * 3600 * 1000).toISOString()
    const newShare: ShareLinkItem = {
      id: `shr-${Date.now()}`,
      token: Math.random().toString(36).substring(2, 10) + Math.random().toString(36).substring(2, 10),
      file_id: fileId,
      filename: target.filename,
      file_size: target.size_bytes,
      expires_at: expires,
      view_count: 0,
      has_password: hasPassword,
      is_revoked: false,
      created_at: new Date().toISOString()
    }
    return {
      shares: [newShare, ...state.shares],
      isShareModalOpen: false,
      shareTargetFile: null
    }
  }),

  revokeShareLink: (shareId) => set((state) => ({
    shares: state.shares.map((s) => s.id === shareId ? { ...s, is_revoked: true } : s)
  }))
}))
