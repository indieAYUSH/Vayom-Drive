import { create } from 'zustand'
import { FileItem, AlbumItem, StorageMetrics, FileCategory } from '../types/vault'
import { api } from '../services/api'

interface VaultState {
  // Data
  files: FileItem[]
  catalogFiles: FileItem[]
  trashCount: number
  albums: AlbumItem[]
  metrics: StorageMetrics
  isLoading: boolean
  error: string | null

  // Navigation & Filtering
  activeTab: 'all' | 'images' | 'videos' | 'screenshots' | 'documents' | 'archives' | 'audio' | 'starred' | 'folders' | 'albums' | 'trash' | 'settings'
  primaryMode: 'photos' | 'drive'
  selectedAlbumId: string | null
  selectedTag: string | null
  searchQuery: string
  activeCategoryFilter: FileCategory | 'all'
  timelineGroupByDate: boolean
  driveViewMode: 'grid' | 'table'

  // Workstation EXIF & Codec Filters
  selectedCamera: string | null
  selectedCodec: string | null
  selectedResolution: string | null
  selectedLens: string | null

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

  // Data Actions
  fetchFiles: () => Promise<void>
  fetchMetrics: () => Promise<void>
  fetchAlbums: () => Promise<void>
  fetchTrashCount: () => Promise<void>
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
  setSelectedCamera: (camera: string | null) => void
  setSelectedCodec: (codec: string | null) => void
  setSelectedResolution: (res: string | null) => void
  setSelectedLens: (lens: string | null) => void
  resetWorkstationFilters: () => void

  toggleStar: (id: string) => void
  setActiveAudioItem: (item: FileItem | null) => void

  toggleSelect: (id: string) => void
  selectIds: (ids: string[]) => void
  deselectIds: (ids: string[]) => void
  selectAll: (ids?: string[]) => void
  clearSelection: () => void

  openLightbox: (item: FileItem) => void
  closeLightbox: () => void
  nextLightbox: () => void
  prevLightbox: () => void

  setCommandBarOpen: (open: boolean) => void
  setUploadModalOpen: (open: boolean) => void
  setCreateAlbumModalOpen: (open: boolean) => void

  // File mutations
  moveToTrash: (ids: string[]) => Promise<void>
  restoreFromTrash: (ids: string[]) => Promise<void>
  purgeFiles: (ids: string[]) => Promise<void>
  emptyTrash: () => Promise<void>
  createAlbum: (name: string, description?: string) => Promise<void>
  rebuildIndex: () => Promise<{ status: string; restored_envelopes: number }>
  cleanupIncomplete: (fileId: string) => Promise<void>
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
  catalogFiles: [],
  trashCount: 0,
  albums: [],
  metrics: {
    total_bytes: 0,
    total_files: 0,
    total_parts: 0,
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

  selectedCamera: null,
  selectedCodec: null,
  selectedResolution: null,
  selectedLens: null,

  starredIds: getInitialStarred(),
  activeAudioItem: null,

  selectedIds: [],
  lightboxItem: null,
  isCommandBarOpen: false,
  isUploadModalOpen: false,
  isCreateAlbumModalOpen: false,

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

      if (isTrash) {
        const files = await api.listFiles({ is_deleted: true, limit: 1000 })
        set({ files, trashCount: files.length, error: null })
        return
      }

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
        is_deleted: false,
        limit: 1000,
      })

      const isGeneralAll =
        activeTab === 'all' &&
        !selectedAlbumId &&
        !selectedTag &&
        !searchQuery.trim() &&
        activeCategoryFilter === 'all'

      set((state) => ({
        files,
        catalogFiles: isGeneralAll ? files : state.catalogFiles.length === 0 ? files : state.catalogFiles,
        error: null,
      }))
    } catch (err: any) {
      set({ error: err.message || 'Failed to fetch files' })
    }
  },

  fetchTrashCount: async () => {
    try {
      const deleted = await api.listFiles({ is_deleted: true, limit: 1000 })
      set({ trashCount: deleted.length })
    } catch {}
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

  init: async () => {
    set({ isLoading: true })
    try {
      const [allActive, deleted, metrics, albums] = await Promise.allSettled([
        api.listFiles({ is_deleted: false, limit: 1000 }),
        api.listFiles({ is_deleted: true, limit: 1000 }),
        api.getMetrics(),
        api.listAlbums(),
      ])

      const activeFiles = allActive.status === 'fulfilled' ? allActive.value : []
      const trashFiles = deleted.status === 'fulfilled' ? deleted.value : []
      const metricsData = metrics.status === 'fulfilled' ? metrics.value : get().metrics
      const albumsData = albums.status === 'fulfilled' ? albums.value : []

      set({
        catalogFiles: activeFiles,
        files: get().activeTab === 'trash' ? trashFiles : activeFiles,
        trashCount: trashFiles.length,
        metrics: metricsData,
        albums: albumsData,
        isLoading: false,
        error: null,
      })
    } catch {
      set({ isLoading: false })
    }
  },

  setActiveTab: (tab) => {
    set({
      activeTab: tab,
      selectedAlbumId: null,
      selectedTag: null,
      selectedIds: [],
      selectedCamera: null,
      selectedCodec: null,
      selectedResolution: null,
      selectedLens: null
    })
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

  setSelectedCamera: (camera) => set({ selectedCamera: camera }),
  setSelectedCodec: (codec) => set({ selectedCodec: codec }),
  setSelectedResolution: (res) => set({ selectedResolution: res }),
  setSelectedLens: (lens) => set({ selectedLens: lens }),
  resetWorkstationFilters: () => set({
    selectedCamera: null,
    selectedCodec: null,
    selectedResolution: null,
    selectedLens: null
  }),

  toggleSelect: (id) => set((state) => {
    const exists = state.selectedIds.includes(id)
    return {
      selectedIds: exists ? state.selectedIds.filter((item) => item !== id) : [...state.selectedIds, id]
    }
  }),

  selectIds: (ids) =>
    set((state) => ({
      selectedIds: Array.from(new Set([...state.selectedIds, ...ids])),
    })),

  deselectIds: (ids) =>
    set((state) => ({
      selectedIds: state.selectedIds.filter((id) => !ids.includes(id)),
    })),

  selectAll: (ids?: string[]) => {
    if (ids && ids.length > 0) {
      set({ selectedIds: ids })
    } else {
      const visibleFiles = get().files.filter((f) => !f.is_deleted)
      set({ selectedIds: visibleFiles.map((f) => f.id) })
    }
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

  moveToTrash: async (ids) => {
    await api.batchTrash(ids)
    set((state) => ({
      selectedIds: [],
      catalogFiles: state.catalogFiles.filter((f) => !ids.includes(f.id)),
      trashCount: state.trashCount + ids.length,
    }))
    await Promise.all([get().fetchFiles(), get().fetchTrashCount(), get().fetchMetrics()])
  },

  restoreFromTrash: async (ids) => {
    await api.batchRestore(ids)
    set((state) => ({
      selectedIds: [],
      trashCount: Math.max(0, state.trashCount - ids.length),
    }))
    await Promise.all([get().fetchFiles(), get().fetchTrashCount(), get().fetchMetrics()])
  },

  purgeFiles: async (ids) => {
    await api.batchPurge(ids)
    set((state) => ({
      selectedIds: [],
      trashCount: Math.max(0, state.trashCount - ids.length),
    }))
    await Promise.all([get().fetchFiles(), get().fetchTrashCount(), get().fetchMetrics()])
  },

  emptyTrash: async () => {
    await api.emptyTrash()
    set({ selectedIds: [], trashCount: 0 })
    await Promise.all([get().fetchFiles(), get().fetchMetrics()])
  },

  createAlbum: async (name, description) => {
    await api.createAlbum(name, description)
    set({ isCreateAlbumModalOpen: false })
    await get().fetchAlbums()
  },

  rebuildIndex: async () => {
    const result = await api.rebuildIndex()
    await Promise.all([get().fetchFiles(), get().fetchMetrics(), get().fetchAlbums(), get().fetchTrashCount()])
    return result
  },

  cleanupIncomplete: async (fileId) => {
    await api.cleanupIncompleteUpload(fileId)
    await Promise.all([get().fetchFiles(), get().fetchMetrics()])
  },
}))
