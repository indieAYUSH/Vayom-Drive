import { create } from 'zustand'
import { FileItem, AlbumItem, StorageMetrics, LicenseStatus } from '../types/vault'
import { api } from '../services/api'
import { useAuthStore } from './useAuthStore'

interface VaultState {
  // Data
  files: FileItem[]
  catalogFiles: FileItem[]
  trashCount: number
  albums: AlbumItem[]
  metrics: StorageMetrics
  license: LicenseStatus | null
  isLoading: boolean
  error: string | null

  // Navigation & Filtering
  activeTab: 'all' | 'images' | 'videos' | 'screenshots' | 'documents' | 'archives' | 'audio' | 'starred' | 'folders' | 'albums' | 'trash' | 'settings'
  primaryMode: 'photos' | 'drive'
  selectedAlbumId: string | null
  selectedTag: string | null
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
  lightboxItems: FileItem[] | null
  isCommandBarOpen: boolean
  isCreateAlbumModalOpen: boolean

  // Pagination & Infinite Scroll (H-19)
  hasMore: boolean
  isLoadingMore: boolean
  fetchMoreFiles: () => Promise<void>

  // Data Actions
  fetchFiles: () => Promise<void>
  refreshCatalog: () => Promise<void>
  fetchMetrics: () => Promise<void>
  fetchAlbums: () => Promise<void>
  fetchTrashCount: () => Promise<void>
  init: () => Promise<void>
  fetchLicense: () => Promise<void>
  activateLicense: (key: string) => Promise<boolean>

  // Navigation Actions
  setActiveTab: (tab: VaultState['activeTab']) => void
  setPrimaryMode: (mode: 'photos' | 'drive') => void
  setSelectedAlbumId: (id: string | null) => void
  setSelectedTag: (tag: string | null) => void
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

  openLightbox: (item: FileItem, contextList?: FileItem[]) => void
  closeLightbox: () => void
  nextLightbox: () => void
  prevLightbox: () => void

  setCommandBarOpen: (open: boolean) => void
  setCreateAlbumModalOpen: (open: boolean) => void

  // File mutations
  moveToTrash: (ids: string[]) => Promise<void>
  restoreFromTrash: (ids: string[]) => Promise<void>
  purgeFiles: (ids: string[]) => Promise<void>
  emptyTrash: () => Promise<void>
  createAlbum: (name: string, description?: string) => Promise<any>
  addFilesToAlbum: (albumId: string, fileIds: string[]) => Promise<void>
  rebuildIndex: () => Promise<{ status: string; restored_envelopes: number }>
  cleanupIncomplete: (fileId: string) => Promise<void>
}

// H-26: Starred ids are validated and namespaced per account/channel so two
// Telegram accounts on the same browser profile don't share favorites.
const STARRED_KEY_PREFIX = 'vayom_starred:'
const STARRED_MIGRATED_FLAG = 'vayom_starred:migrated'

const starredNamespace = (auth = useAuthStore.getState()): string =>
  auth.channelId || (auth.user?.id != null ? String(auth.user.id) : 'default')

const sanitizeStarred = (raw: string): string[] => {
  try {
    const parsed = JSON.parse(raw)
    return Array.isArray(parsed) ? parsed.filter((x): x is string => typeof x === 'string') : []
  } catch {
    return []
  }
}

// Returns null when the namespaced key has never been written (caller may migrate legacy data)
const readNamespacedStarred = (namespace: string): string[] | null => {
  try {
    const raw = localStorage.getItem(`${STARRED_KEY_PREFIX}${namespace}`)
    return raw === null ? null : sanitizeStarred(raw)
  } catch {
    return null
  }
}

// H-10: Request sequencing & cancellation to prevent stale-wins race conditions
const PAGE_SIZE = 150
let fetchFilesController: AbortController | null = null
let currentFetchEpoch = 0

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
  license: null,
  isLoading: false,
  error: null,

  hasMore: false,
  isLoadingMore: false,

  activeTab: 'all',
  primaryMode: 'photos',
  selectedAlbumId: null,
  selectedTag: null,
  timelineGroupByDate: true,
  driveViewMode: 'grid',

  selectedCamera: null,
  selectedCodec: null,
  selectedResolution: null,
  selectedLens: null,

  starredIds: [],
  activeAudioItem: null,

  selectedIds: [],
  lightboxItem: null,
  lightboxItems: null,
  isCommandBarOpen: false,
  isCreateAlbumModalOpen: false,

  setPrimaryMode: (mode) => set({ primaryMode: mode }),
  setTimelineGroupByDate: (group) => set({ timelineGroupByDate: group }),
  setDriveViewMode: (mode) => set({ driveViewMode: mode }),
  toggleStar: (id) =>
    set((state) => {
      const exists = state.starredIds.includes(id)
      const updated = exists ? state.starredIds.filter((item) => item !== id) : [...state.starredIds, id]
      try {
        localStorage.setItem(`${STARRED_KEY_PREFIX}${starredNamespace()}`, JSON.stringify(updated))
      } catch {}
      return { starredIds: updated }
    }),
  setActiveAudioItem: (item) => set({ activeAudioItem: item }),

  fetchFiles: async () => {
    if (fetchFilesController) {
      fetchFilesController.abort()
    }
    fetchFilesController = new AbortController()
    const signal = fetchFilesController.signal
    const epoch = ++currentFetchEpoch

    try {
      const { activeTab, selectedAlbumId, selectedTag } = get()
      const isTrash = activeTab === 'trash'

      if (isTrash) {
        const files = await api.listFiles({ is_deleted: true, limit: PAGE_SIZE, offset: 0 }, { signal })
        if (epoch !== currentFetchEpoch || signal.aborted) return
        set({ files, trashCount: files.length, hasMore: files.length === PAGE_SIZE, isLoadingMore: false, error: null })
        return
      }

      let cat: string | undefined = undefined
      if (activeTab === 'images') cat = 'image'
      else if (activeTab === 'videos') cat = 'video'
      else if (activeTab === 'documents') cat = 'document'
      else if (activeTab === 'archives') cat = 'archive'

      const isFolderTab = activeTab === 'folders' || activeTab === 'albums'
      const files = await api.listFiles({
        category: cat,
        tag: selectedTag || undefined,
        album_id: isFolderTab && selectedAlbumId ? selectedAlbumId : undefined,
        is_deleted: false,
        limit: PAGE_SIZE,
        offset: 0,
      }, { signal })

      if (epoch !== currentFetchEpoch || signal.aborted) return

      const isGeneralAll = activeTab === 'all' && !selectedAlbumId && !selectedTag

      set((state) => ({
        files,
        catalogFiles: isGeneralAll ? files : state.catalogFiles.length === 0 ? files : state.catalogFiles,
        hasMore: files.length === PAGE_SIZE,
        isLoadingMore: false,
        error: null,
      }))
    } catch (err: any) {
      if (err?.name === 'AbortError' || epoch !== currentFetchEpoch) return
      set({ error: err.message || 'Failed to fetch files', isLoadingMore: false })
    }
  },

  fetchMoreFiles: async () => {
    const { isLoadingMore, hasMore, files, activeTab, selectedAlbumId, selectedTag } = get()
    if (isLoadingMore || !hasMore) return

    set({ isLoadingMore: true })
    try {
      const isTrash = activeTab === 'trash'
      let cat: string | undefined = undefined
      if (activeTab === 'images') cat = 'image'
      else if (activeTab === 'videos') cat = 'video'
      else if (activeTab === 'documents') cat = 'document'
      else if (activeTab === 'archives') cat = 'archive'

      const isFolderTab = activeTab === 'folders' || activeTab === 'albums'
      const newFiles = await api.listFiles({
        category: cat,
        tag: selectedTag || undefined,
        album_id: isFolderTab && selectedAlbumId ? selectedAlbumId : undefined,
        is_deleted: isTrash,
        limit: PAGE_SIZE,
        offset: files.length,
      })

      if (newFiles.length === 0) {
        set({ hasMore: false, isLoadingMore: false })
        return
      }

      set((state) => {
        const existingIds = new Set(state.files.map((f) => f.id))
        const deduplicated = newFiles.filter((f) => !existingIds.has(f.id))
        return {
          files: [...state.files, ...deduplicated],
          hasMore: newFiles.length === PAGE_SIZE,
          isLoadingMore: false,
        }
      })
    } catch (err) {
      set({ isLoadingMore: false })
    }
  },

  // H-28: Refresh the unfiltered vault-wide catalog (drives sidebar counts) on upload completion
  refreshCatalog: async () => {
    try {
      const catalog = await api.listFiles({ is_deleted: false, limit: 1000 })
      set({ catalogFiles: catalog })
    } catch {}
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
    const epoch = ++currentFetchEpoch
    try {
      const [allActive, deleted, metrics, albums, license] = await Promise.allSettled([
        api.listFiles({ is_deleted: false, limit: 1000 }),
        api.listFiles({ is_deleted: true, limit: 1000 }),
        api.getMetrics(),
        api.listAlbums(),
        api.getLicenseStatus(),
      ])

      const activeFiles = allActive.status === 'fulfilled' ? allActive.value : []
      const trashFiles = deleted.status === 'fulfilled' ? deleted.value : []
      const metricsData = metrics.status === 'fulfilled' ? metrics.value : get().metrics
      const albumsData = albums.status === 'fulfilled' ? albums.value : []
      const licenseData = license.status === 'fulfilled' ? license.value : null

      // H-47: a newer fetch superseded this init — don't clobber its results
      if (epoch !== currentFetchEpoch) {
        set({ isLoading: false })
        return
      }

      set({
        catalogFiles: activeFiles,
        files: get().activeTab === 'trash' ? trashFiles : activeFiles,
        trashCount: trashFiles.length,
        metrics: metricsData,
        albums: albumsData,
        license: licenseData,
        isLoading: false,
        error: null,
      })
    } catch {
      set({ isLoading: false })
    }
  },

  fetchLicense: async () => {
    try {
      const license = await api.getLicenseStatus()
      set({ license })
    } catch (err) {
      console.error('Failed to fetch license status', err)
    }
  },

  activateLicense: async (key: string) => {
    try {
      const res = await api.activateLicense(key)
      if (res.license) {
        set({ license: res.license })
        return true
      }
      return false
    } catch (err) {
      console.error('Failed to activate license', err)
      throw err
    }
  },

  setActiveTab: (tab) => {
    const prev = get()
    // H-36: skip the redundant refetch when the effective query is unchanged
    const queryUnchanged =
      prev.activeTab === tab && prev.selectedAlbumId === null && prev.selectedTag === null
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
    if (!queryUnchanged) get().fetchFiles()
  },

  setSelectedAlbumId: (id) => {
    const prev = get()
    const queryUnchanged = prev.selectedAlbumId === id && prev.activeTab === 'folders'
    set({ selectedAlbumId: id, activeTab: 'folders', selectedIds: [] })
    if (!queryUnchanged) get().fetchFiles()
  },

  setSelectedTag: (tag) => {
    const prev = get()
    const queryUnchanged = prev.selectedTag === tag
    set({ selectedTag: tag, selectedIds: [] })
    if (!queryUnchanged) get().fetchFiles()
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

  openLightbox: (item, contextList) => set({ lightboxItem: item, lightboxItems: contextList ?? null }),
  closeLightbox: () => set({ lightboxItem: null, lightboxItems: null }),
  nextLightbox: () => {
    const { files, lightboxItem, lightboxItems } = get()
    if (!lightboxItem) return
    const activeList = lightboxItems && lightboxItems.length > 0 ? lightboxItems : files.filter((f) => !f.is_deleted)
    const currentIndex = activeList.findIndex((f) => f.id === lightboxItem.id)
    if (currentIndex >= 0 && currentIndex < activeList.length - 1) {
      set({ lightboxItem: activeList[currentIndex + 1] })
    }
  },
  prevLightbox: () => {
    const { files, lightboxItem, lightboxItems } = get()
    if (!lightboxItem) return
    const activeList = lightboxItems && lightboxItems.length > 0 ? lightboxItems : files.filter((f) => !f.is_deleted)
    const currentIndex = activeList.findIndex((f) => f.id === lightboxItem.id)
    if (currentIndex > 0) {
      set({ lightboxItem: activeList[currentIndex - 1] })
    }
  },

  setCommandBarOpen: (open) => set({ isCommandBarOpen: open }),
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
    await Promise.all([get().fetchFiles(), get().fetchTrashCount(), get().fetchMetrics(), get().refreshCatalog()])
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
    const album = await api.createAlbum(name, description)
    await get().fetchAlbums()
    return album
  },

  addFilesToAlbum: async (albumId, fileIds) => {
    await Promise.all(fileIds.map((id) => api.addFileToAlbum(albumId, id)))
    set({ selectedIds: [], isCreateAlbumModalOpen: false })
    await Promise.all([get().fetchFiles(), get().fetchAlbums()])
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

// H-26: Reload (or clear) starred ids whenever the active account/channel changes.
// Legacy global keys are migrated once, then writes are namespaced per account.
useAuthStore.subscribe((state, prev) => {
  const sessionChanged =
    state.isAuthenticated !== prev.isAuthenticated || state.channelId !== prev.channelId
  if (!sessionChanged) return

  if (!state.isAuthenticated) {
    useVaultStore.setState({ starredIds: [] })
    return
  }

  const namespace = starredNamespace(state)
  let ids = readNamespacedStarred(namespace)
  if (ids === null) {
    // First login for this namespace: migrate legacy global stars once
    let legacy: string | null = null
    let alreadyMigrated = false
    try {
      legacy = localStorage.getItem('vayom_starred') ?? localStorage.getItem('televault_starred')
      alreadyMigrated = localStorage.getItem(STARRED_MIGRATED_FLAG) !== null
    } catch {}
    ids = legacy !== null && !alreadyMigrated ? sanitizeStarred(legacy) : []
    try {
      localStorage.setItem(`${STARRED_KEY_PREFIX}${namespace}`, JSON.stringify(ids))
      localStorage.setItem(STARRED_MIGRATED_FLAG, '1')
    } catch {}
  }
  useVaultStore.setState({ starredIds: ids })
})
