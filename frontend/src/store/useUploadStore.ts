import { create } from 'zustand'
import { UploadTask } from '../types/upload'
import { AlbumItem } from '../types/vault'
import { useVaultStore } from './useVaultStore'
import { api, API_BASE } from '../services/api'

interface UploadState {
  tasks: UploadTask[]
  isTrayOpen: boolean
  floodWaitSecondsRemaining: number
  folderPrompt: { folderName: string; files: File[] } | null

  selectedBackend: 'telegram' | 'nas' | 's3' | 'nextcloud'
  setSelectedBackend: (backend: 'telegram' | 'nas' | 's3' | 'nextcloud') => void
  setTrayOpen: (open: boolean) => void
  setFolderPrompt: (prompt: { folderName: string; files: File[] } | null) => void
  addFilesToUpload: (files: FileList | File[], targetAlbumId?: string | null) => void
  uploadFolderAsAlbum: (folderName: string, files: File[]) => Promise<void>
  compressFolderToZipAndUpload: (folderName: string, files: File[]) => Promise<void>
  pauseTask: (id: string) => void
  resumeTask: (id: string) => void
  retryTask: (id: string) => void
  cancelTask: (id: string) => void
  clearCompleted: () => void
  clearFailed: () => void
}

// Map of active XMLHttpRequest instances for pause/cancellation
const activeUploads = new Map<string, XMLHttpRequest>()
const activePollIntervals = new Map<string, any>()
// H-41: chunked tasks with a live worker (claimed synchronously, released in finally)
const activeChunkWorkers = new Set<string>()
// H-39: prevents recursive re-entry of the flood-wait requeue path
let isRequeuingRateLimited = false

// Anti-Ban Invariant 4: Limit upload concurrency to 3-4 parallel workers
const MAX_CONCURRENT_UPLOADS = 3
let floodWaitCooldownUntil = 0
let floodWaitTimeoutId: any = null
let floodWaitTickerId: any = null

// H-16: share one in-flight album-create promise per folder name so concurrent
// completions from the same directory drop cannot create duplicate albums
const albumCreatePromises = new Map<string, Promise<AlbumItem>>()

export function findOrCreateAlbum(folderName: string): Promise<AlbumItem> {
  const cleanName = folderName.trim()
  const key = cleanName.toLowerCase()
  const existing = useVaultStore.getState().albums.find((a) => a.name.toLowerCase() === key)
  if (existing) {
    albumCreatePromises.delete(key)
    return Promise.resolve(existing)
  }
  let promise = albumCreatePromises.get(key)
  if (!promise) {
    promise = api.createAlbum(cleanName).then((newAlbum) => {
      useVaultStore.getState().fetchAlbums()
      return newAlbum
    })
    promise.catch(() => albumCreatePromises.delete(key))
    albumCreatePromises.set(key, promise)
  }
  return promise
}

// H-55: associate a completed upload with the album captured at enqueue time,
// falling back to the dropped folder's top-level name. Shared by both the
// single-part and chunked completion paths so they can't diverge.
async function associateUploadedFile(fileId: string, task: UploadTask) {
  const albumId = task.target_album_id
  if (albumId) {
    try {
      await api.addFileToAlbum(albumId, fileId)
    } catch {}
    return
  }
  const rel = ((task.file as any).customRelativePath || (task.file as any).webkitRelativePath) as string | undefined
  if (!rel) return
  const parts = rel.split('/')
  if (parts.length <= 1) return
  // H-16: concurrent completions from one directory drop share a single create
  try {
    const album = await findOrCreateAlbum(parts[0])
    await api.addFileToAlbum(album.id, fileId)
    useVaultStore.getState().fetchAlbums()
  } catch {}
}

function updateFloodWaitTicker() {
  const now = Date.now()
  if (floodWaitCooldownUntil > now) {
    const remaining = Math.ceil((floodWaitCooldownUntil - now) / 1000)
    useUploadStore.setState({ floodWaitSecondsRemaining: remaining })
    if (!floodWaitTickerId) {
      floodWaitTickerId = setInterval(updateFloodWaitTicker, 1000)
    }
  } else {
    useUploadStore.setState({ floodWaitSecondsRemaining: 0 })
    if (floodWaitTickerId) {
      clearInterval(floodWaitTickerId)
      floodWaitTickerId = null
    }
  }
}

function requeueAllRateLimitedTasks() {
  // H-39: aborting an xhr runs its handler synchronously, and that handler calls
  // processQueue() → this function again. Without the guard the inner call would
  // requeue and restart workers while the outer loop is still aborting/deleting.
  if (isRequeuingRateLimited) return

  const state = useUploadStore.getState()
  const rateLimitedIds = new Set(
    state.tasks.filter((t) => t.status === 'rate_limited').map((t) => t.id)
  )

  if (rateLimitedIds.size > 0) {
    isRequeuingRateLimited = true
    try {
      for (const id of rateLimitedIds) {
        // Clear the old poll interval *before* aborting so a re-entrant restart
        // can install a fresh one without this cleanup removing it.
        const poll = activePollIntervals.get(id)
        if (poll) {
          clearInterval(poll)
          activePollIntervals.delete(id)
        }

        const xhr = activeUploads.get(id)
        if (xhr) {
          xhr.abort()
          // H-39: only remove our own registration — the abort handler may
          // already have started and registered a newer worker for this task.
          if (activeUploads.get(id) === xhr) {
            activeUploads.delete(id)
          }
        }
      }

      // Requeue only after every abort has finished unwinding so no worker can
      // start while its predecessor's abort handler is still on the stack.
      useUploadStore.setState((s) => ({
        tasks: s.tasks.map((t) =>
          rateLimitedIds.has(t.id)
            ? { ...t, status: 'queued', error_message: undefined, speed_bps: 0 }
            : t
        ),
      }))
    } finally {
      isRequeuingRateLimited = false
    }
  }

  updateFloodWaitTicker()
  processQueue()
}

function scheduleFloodWaitCooldown(cooldownUntil: number) {
  const now = Date.now()
  if (cooldownUntil <= now) {
    if (floodWaitTimeoutId) {
      clearTimeout(floodWaitTimeoutId)
      floodWaitTimeoutId = null
    }
    floodWaitCooldownUntil = 0
    requeueAllRateLimitedTasks()
    return
  }

  // Only extend if no timer exists or if new cooldown is significantly further out (>1500ms)
  // to avoid 250ms polling jitter constantly resetting the timer
  if (!floodWaitTimeoutId || cooldownUntil > floodWaitCooldownUntil + 1500) {
    floodWaitCooldownUntil = Math.max(floodWaitCooldownUntil, cooldownUntil)
    if (floodWaitTimeoutId) {
      clearTimeout(floodWaitTimeoutId)
    }
    const remaining = Math.max(0, floodWaitCooldownUntil - Date.now())
    floodWaitTimeoutId = setTimeout(() => {
      floodWaitTimeoutId = null
      floodWaitCooldownUntil = 0
      requeueAllRateLimitedTasks()
    }, remaining)
  }

  updateFloodWaitTicker()
}

function processQueue() {
  const now = Date.now()
  if (floodWaitCooldownUntil > now) {
    scheduleFloodWaitCooldown(floodWaitCooldownUntil)
    return
  }

  // If cooldown is expired but rate_limited tasks remain, requeue them all
  const state = useUploadStore.getState()
  const hasRateLimited = state.tasks.some((t) => t.status === 'rate_limited')
  if (hasRateLimited) {
    requeueAllRateLimitedTasks()
    return
  }

  const activeCount = state.tasks.filter(
    (t) =>
      t.status === 'uploading' ||
      t.status === 'buffering' ||
      t.status === 'hashing' ||
      t.status === 'analyzing' ||
      t.status === 'splitting' ||
      t.status === 'verifying'
  ).length

  const availableSlots = MAX_CONCURRENT_UPLOADS - activeCount
  if (availableSlots <= 0) return

  const queuedTasks = state.tasks.filter((t) => t.status === 'queued').slice(0, availableSlots)
  for (const task of queuedTasks) {
    const isTelegram = !task.storage_backend || task.storage_backend === 'telegram'
    if (isTelegram && (task.total_parts > 1 || task.size_bytes > 100 * 1024 * 1024)) {
      executeChunkedUpload(task.id)
    } else {
      executeRealUpload(task.id)
    }
  }
}

export const useUploadStore = create<UploadState>((set, get) => ({
  tasks: [],
  isTrayOpen: true,
  floodWaitSecondsRemaining: 0,
  folderPrompt: null,
  selectedBackend: 'telegram',

  setSelectedBackend: (backend) => set({ selectedBackend: backend }),
  setTrayOpen: (open) => set({ isTrayOpen: open }),
  setFolderPrompt: (prompt) => set({ folderPrompt: prompt }),

  uploadFolderAsAlbum: async (folderName: string, files: File[]) => {
    try {
      const album = await findOrCreateAlbum(folderName)
      get().addFilesToUpload(files, album.id)
      set({ folderPrompt: null })
      useVaultStore.getState().fetchAlbums()
    } catch (e) {
      console.error('Failed to create album for folder upload:', e)
      get().addFilesToUpload(files)
      set({ folderPrompt: null })
    }
  },

  compressFolderToZipAndUpload: async (folderName: string, files: File[]) => {
    try {
      const JSZipModule = (await import('jszip')).default
      const zip = new JSZipModule()
      for (const file of files) {
        const rel = (file as any).customRelativePath || file.webkitRelativePath || file.name
        const parts = rel.split('/')
        const internalPath = parts.length > 1 ? parts.slice(1).join('/') : file.name
        zip.file(internalPath, file)
      }

      const zipBlob = await zip.generateAsync({
        type: 'blob',
        compression: 'DEFLATE',
        compressionOptions: { level: 6 },
      })

      const zipFile = new File([zipBlob], `${folderName}.zip`, {
        type: 'application/zip',
        lastModified: Date.now(),
      })

      get().addFilesToUpload([zipFile])
      set({ folderPrompt: null })
    } catch (err) {
      console.error('Failed to create zip file:', err)
      get().addFilesToUpload(files)
      set({ folderPrompt: null })
    }
  },

  addFilesToUpload: (fileInput, explicitAlbumId) => {
    const fileArray = Array.from(fileInput)
    if (fileArray.length === 0) return

    const currentBackend = get().selectedBackend
    // Priority: explicitly passed albumId > selectedAlbumId
    const targetAlbumId = explicitAlbumId !== undefined ? explicitAlbumId : (useVaultStore.getState().selectedAlbumId ?? null)
    const newTasks: UploadTask[] = fileArray.map((file) => {
      const isLarge = file.size > 1887436800
      const totalParts = (isLarge && currentBackend === 'telegram') ? Math.ceil(file.size / 1887436800) : 1
      return {
        id: `upl-${Date.now()}-${Math.random().toString(36).substring(2, 7)}`,
        file,
        filename: file.name,
        size_bytes: file.size,
        uploaded_bytes: 0,
        progress_percent: 0,
        speed_bps: 0,
        eta_seconds: 0,
        status: 'queued',
        current_part: 1,
        total_parts: totalParts,
        storage_backend: currentBackend,
        target_album_id: targetAlbumId,
        created_at: Date.now(),
      }
    })

    set((state) => ({
      tasks: [...state.tasks, ...newTasks],
      isTrayOpen: true,
    }))

    // Process worker queue respecting MAX_CONCURRENT_UPLOADS limit
    processQueue()
  },

  pauseTask: (id) => {
    const xhr = activeUploads.get(id)
    const pollInterval = activePollIntervals.get(id)
    if (pollInterval) {
      clearInterval(pollInterval)
      activePollIntervals.delete(id)
    }
    // Mark paused before aborting so a re-entrant callback cannot restart it.
    set((state) => ({
      tasks: state.tasks.map((t) => (t.id === id ? { ...t, status: 'paused', speed_bps: 0 } : t)),
    }))
    if (xhr) {
      xhr.abort()
      // H-39: only remove our own registration, never a newer worker's.
      if (activeUploads.get(id) === xhr) {
        activeUploads.delete(id)
      }
    }
    processQueue()
  },

  resumeTask: (id) => {
    const xhr = activeUploads.get(id)
    const pollInterval = activePollIntervals.get(id)
    if (pollInterval) {
      clearInterval(pollInterval)
      activePollIntervals.delete(id)
    }
    if (xhr) {
      xhr.abort()
      if (activeUploads.get(id) === xhr) {
        activeUploads.delete(id)
      }
    }
    set((state) => ({
      tasks: state.tasks.map((t) =>
        t.id === id ? { ...t, status: 'queued', error_message: undefined } : t
      ),
    }))
    processQueue()
  },

  cancelTask: (id) => {
    const task = get().tasks.find((t) => t.id === id)
    const xhr = activeUploads.get(id)
    const pollInterval = activePollIntervals.get(id)
    if (pollInterval) {
      clearInterval(pollInterval)
      activePollIntervals.delete(id)
    }

    // Remove the task first so no abort/poll callback can restart it.
    set((state) => ({
      tasks: state.tasks.filter((t) => t.id !== id),
    }))

    if (xhr) {
      xhr.abort()
      if (activeUploads.get(id) === xhr) {
        activeUploads.delete(id)
      }
    }

    // H-6: Call cleanupIncompleteUpload to purge orphan Telegram message parts
    if (task?.server_file_id) {
      api.cleanupIncompleteUpload(task.server_file_id).catch(() => {})
    }

    processQueue()
  },

  // H-31: Re-queue a failed task so the worker pool retries it
  // (chunked uploads resume from known parts via server_file_id)
  retryTask: (id) => {
    const task = get().tasks.find((t) => t.id === id)
    if (!task || task.status === 'done') return
    set((state) => ({
      tasks: state.tasks.map((t) =>
        t.id === id ? { ...t, status: 'queued', error_message: undefined, speed_bps: 0, eta_seconds: 0 } : t
      ),
      isTrayOpen: true,
    }))
    processQueue()
  },

  clearCompleted: () =>
    set((state) => ({
      tasks: state.tasks.filter((t) => t.status !== 'done'),
    })),

  // H-31: Drop failed tasks (and their retained multi-GB File objects) from memory
  clearFailed: () =>
    set((state) => ({
      tasks: state.tasks.filter((t) => t.status !== 'failed'),
    })),
}))

function executeRealUpload(taskId: string) {
  const updateTask = (updates: Partial<UploadTask>) => {
    useUploadStore.setState((state) => ({
      tasks: state.tasks.map((t) => (t.id === taskId ? { ...t, ...updates } : t)),
    }))
  }

  const task = useUploadStore.getState().tasks.find((t) => t.id === taskId)
  if (!task) return

  updateTask({
    status: 'buffering',
    stage: 'Buffering to node gateway...',
    progress_percent: 0,
    uploaded_bytes: 0,
    speed_bps: 0,
    eta_seconds: 0,
  })

  const xhr = new XMLHttpRequest()
  activeUploads.set(taskId, xhr)

  let pollInterval: any = null
  const cleanupPolling = () => {
    // H-39: only clear the interval installed by *this* worker. A restarted
    // worker may have installed a fresh interval for the same task id.
    if (pollInterval && activePollIntervals.get(taskId) === pollInterval) {
      clearInterval(pollInterval)
      activePollIntervals.delete(taskId)
    }
  }

  // Poll real-time MTProto upload progress from backend every 300ms with concurrency guard
  let isPolling = false
  pollInterval = setInterval(async () => {
    if (isPolling) return
    isPolling = true
    try {
      const controller = new AbortController()
      const timeoutId = setTimeout(() => controller.abort(), 2500)
      const res = await fetch(`${API_BASE}/upload/${encodeURIComponent(taskId)}/progress`, {
        signal: controller.signal,
      })
      clearTimeout(timeoutId)
      if (!res.ok) return
      const data = await res.json()
      if (data && data.status && data.status !== 'unknown') {
        const currentTask = useUploadStore.getState().tasks.find((t) => t.id === taskId)
        if (!currentTask || currentTask.status === 'paused' || currentTask.status === 'done' || currentTask.status === 'failed') {
          cleanupPolling()
          return
        }

        const updates: Partial<UploadTask> = {}
        if (data.file_id) updates.server_file_id = data.file_id
        if (data.stage) updates.stage = data.stage
        if (data.current_part) updates.current_part = data.current_part
        if (data.total_parts) updates.total_parts = data.total_parts

        if (data.status === 'rate_limited') {
          const waitSec = data.flood_wait_seconds || 10
          const waitPenalty = waitSec + 2
          const cooldownUntil = Date.now() + waitPenalty * 1000
          updates.status = 'rate_limited'
          updates.error_message = data.stage || `Telegram Rate-Limit active: Pausing for ${Math.ceil(waitSec)}s to safeguard account...`
          updates.speed_bps = 0
          scheduleFloodWaitCooldown(cooldownUntil)
        } else if (data.status === 'done') {
          updates.status = 'done'
          updates.stage = 'Upload completed'
          updates.progress_percent = 100
          updates.uploaded_bytes = task.size_bytes
          updates.speed_bps = 0
          updates.eta_seconds = 0
          updates.error_message = undefined
          cleanupPolling()
        } else if (data.status === 'verifying') {
          updates.status = 'verifying'
          updates.stage = data.stage || 'Syncing encrypted master catalog to Telegram WAL...'
          updates.progress_percent = 99
          updates.uploaded_bytes = task.size_bytes
          updates.speed_bps = 0
          updates.eta_seconds = 0
          updates.error_message = undefined
        } else if (data.status === 'hashing') {
          updates.status = 'hashing'
          updates.stage = data.stage || 'Computing SHA-256 integrity hash...'
          updates.speed_bps = 0
          updates.eta_seconds = 0
        } else if (data.status === 'analyzing') {
          updates.status = 'analyzing'
          updates.stage = data.stage || 'Extracting media metadata & streaming headers...'
          updates.speed_bps = 0
          updates.eta_seconds = 0
        } else if (data.status === 'splitting') {
          updates.status = 'splitting'
          updates.stage = data.stage || 'Preparing MTProto upload slices...'
          updates.speed_bps = 0
          updates.eta_seconds = 0
        } else if (data.status === 'uploading') {
          updates.status = 'uploading'
          updates.error_message = undefined
          if (typeof data.uploaded_bytes === 'number') {
            updates.uploaded_bytes = data.uploaded_bytes
          }
          if (typeof data.speed_bps === 'number') {
            updates.speed_bps = data.speed_bps
          }
          if (typeof data.eta_seconds === 'number') {
            updates.eta_seconds = data.eta_seconds
          }
          if (typeof data.progress_percent === 'number') {
            updates.progress_percent = Math.min(99, Math.max(0, data.progress_percent))
          }
        } else if (data.status === 'failed') {
          const activeXhr = activeUploads.get(taskId)
          if (activeXhr) {
            activeXhr.abort()
            // H-39: never delete a newer worker's registration.
            if (activeUploads.get(taskId) === activeXhr) {
              activeUploads.delete(taskId)
            }
          }
          cleanupPolling()
          updates.status = 'failed'
          updates.error_message = data.error || data.stage || 'Upload failed'
          updates.speed_bps = 0
          const currentTask = useUploadStore.getState().tasks.find((t) => t.id === taskId)
          if (currentTask?.server_file_id) {
            api.cleanupIncompleteUpload(currentTask.server_file_id).catch(() => {})
          }
          processQueue()
        }

        updateTask(updates)
      }
    } catch {} finally {
      isPolling = false
    }
  }, 300)

  activePollIntervals.set(taskId, pollInterval)

  // Track client-to-gateway buffer transfer (0% to 100% of buffer phase)
  let lastLoaded = 0
  let lastTime = Date.now()
  let lastUiUpdate = 0

  xhr.upload.onprogress = (event) => {
    if (event.lengthComputable) {
      const now = Date.now()
      if (now - lastUiUpdate < 200 && event.loaded < event.total) {
        return
      }
      lastUiUpdate = now

      const timeDiff = (now - lastTime) / 1000
      let speed = 0
      if (timeDiff >= 0.2) {
        speed = Math.round((event.loaded - lastLoaded) / timeDiff)
        lastLoaded = event.loaded
        lastTime = now
      }

      const currentTask = useUploadStore.getState().tasks.find((t) => t.id === taskId)
      // Only display client buffer progress while file is being ingested to gateway
      if (currentTask && (currentTask.status === 'buffering' || currentTask.status === 'queued')) {
        const bufferPct = Math.min(100, Math.round((event.loaded / event.total) * 100))
        const eta = speed > 0 ? Math.round((event.total - event.loaded) / speed) : 0
        updateTask({
          progress_percent: bufferPct,
          uploaded_bytes: event.loaded,
          speed_bps: speed,
          eta_seconds: eta,
          status: 'buffering',
          stage: event.loaded >= event.total ? 'Gateway ingestion complete • Processing media on node...' : 'Buffering to node gateway...',
        })
      }
    }
  }

  xhr.onload = () => {
    cleanupPolling()
    // H-39: identity guard — a flood-wait restart may already have registered
    // a newer xhr for this task.
    if (activeUploads.get(taskId) === xhr) {
      activeUploads.delete(taskId)
    }
    if (xhr.status >= 200 && xhr.status < 300) {
      updateTask({
        status: 'done',
        stage: 'Upload completed',
        progress_percent: 100,
        uploaded_bytes: task.size_bytes,
        speed_bps: 0,
        eta_seconds: 0,
        error_message: undefined,
      })

      // Auto-associate with current folder or dropped directory structure
      try {
        const json = JSON.parse(xhr.responseText)
        if (json.file_id) {
          updateTask({ server_file_id: json.file_id })
        }
        // H-55: use the album captured when the task was enqueued (plus the
        // dropped-folder fallback), not whatever is selected right now.
        if (json.file_id) {
          associateUploadedFile(json.file_id, task)
        }
      } catch {}

      // Refresh files catalog and metrics in vault store
      useVaultStore.getState().fetchFiles()
      useVaultStore.getState().fetchAlbums()
      useVaultStore.getState().fetchMetrics()
      useVaultStore.getState().refreshCatalog()
      processQueue()
    } else {
      // Anti-Ban Invariant 5: Strict Global FloodWait Handling in Frontend Queue
      let floodWaitSec = 0
      try {
        const errJson = JSON.parse(xhr.responseText)
        if (errJson.flood_wait_seconds) {
          floodWaitSec = errJson.flood_wait_seconds
        } else if (typeof errJson.detail === 'string' && errJson.detail.includes('FLOOD_WAIT_')) {
          const match = errJson.detail.match(/FLOOD_WAIT_(\d+)/)
          if (match) floodWaitSec = parseInt(match[1], 10)
        }
      } catch {}
      if (!floodWaitSec) {
        const retryAfter = parseInt(xhr.getResponseHeader('Retry-After') || '0', 10)
        if (retryAfter > 0) floodWaitSec = retryAfter
      }

      if (xhr.status === 429 || floodWaitSec > 0) {
        const waitPenalty = (floodWaitSec || 10) + 2
        const cooldownUntil = Date.now() + waitPenalty * 1000
        updateTask({
          status: 'rate_limited',
          error_message: `Telegram FloodWait: pausing queue for ${waitPenalty}s to safeguard account...`,
          speed_bps: 0,
        })
        scheduleFloodWaitCooldown(cooldownUntil)
        return
      }

      if (xhr.status === 402) {
        let msg = 'Pro license required for this storage destination.'
        try {
          const err = JSON.parse(xhr.responseText)
          if (err.detail?.message) msg = err.detail.message
        } catch {}
        updateTask({
          status: 'failed',
          error_message: msg,
          speed_bps: 0,
        })
        processQueue()
        return
      }

      updateTask({
        status: 'failed',
        error_message: `Upload failed: ${xhr.status} ${xhr.statusText}`,
        speed_bps: 0,
      })
      processQueue()
    }
  }

  xhr.onerror = () => {
    cleanupPolling()
    if (activeUploads.get(taskId) === xhr) {
      activeUploads.delete(taskId)
    }
    updateTask({
      status: 'failed',
      error_message: 'Network connection error during MTProto upload',
      speed_bps: 0,
    })
    processQueue()
  }

  xhr.onabort = () => {
    cleanupPolling()
    // H-39: only remove our own registration; a re-entrant restart may have
    // already registered a replacement xhr for this task id.
    if (activeUploads.get(taskId) === xhr) {
      activeUploads.delete(taskId)
    }
    processQueue()
  }

  const formData = new FormData()
  formData.append('file', task.file)
  formData.append('task_id', taskId)
  if (task.target_album_id) {
    formData.append('album_id', task.target_album_id)
  }

  xhr.open('POST', `${API_BASE}/upload?task_id=${encodeURIComponent(taskId)}`)
  xhr.setRequestHeader('X-Task-ID', taskId)
  if (task.storage_backend) {
    xhr.setRequestHeader('X-Storage-Backend', task.storage_backend)
  }
  if (task.target_album_id) {
    xhr.setRequestHeader('X-Album-ID', task.target_album_id)
  }
  xhr.send(formData)
}

// H-41: chunked worker entry point. The guard is claimed synchronously before
// any await so two processQueue() passes cannot run the same task in parallel.
async function executeChunkedUpload(taskId: string) {
  if (activeChunkWorkers.has(taskId)) return
  activeChunkWorkers.add(taskId)
  try {
    await executeChunkedUploadInner(taskId)
  } finally {
    activeChunkWorkers.delete(taskId)
    // If the task was re-queued while the old worker was still unwinding
    // (pause → resume, or a flood restart), make sure it gets a new worker.
    const current = useUploadStore.getState().tasks.find((t) => t.id === taskId)
    if (current?.status === 'queued') {
      processQueue()
    }
  }
}

// H-5: Resumable chunked upload worker for multi-part files
async function executeChunkedUploadInner(taskId: string) {
  const updateTask = (updates: Partial<UploadTask>) => {
    useUploadStore.setState((state) => ({
      tasks: state.tasks.map((t) => (t.id === taskId ? { ...t, ...updates } : t)),
    }))
  }

  const task = useUploadStore.getState().tasks.find((t) => t.id === taskId)
  if (!task) return

  const file = task.file
  const PART_SIZE = 1887436800 // 900 * 2 MiB boundary, exact multiple of 2 MiB, 512 KiB, 4096 bytes
  const totalParts = Math.max(1, Math.ceil(file.size / PART_SIZE))

  let fileId = task.server_file_id

  // 1. Initialize or query resumable upload session on backend
  let uploadedPartsList: number[] = []
  if (!fileId) {
    updateTask({
      status: 'buffering',
      stage: 'Initializing chunked upload session...',
      progress_percent: 1,
    })
    try {
      const initRes = await api.initUpload({
        filename: file.name,
        total_size: file.size,
        mime_type: file.type || 'application/octet-stream',
        total_parts: totalParts,
        album_id: task.target_album_id,
      })
      fileId = initRes.file_id
      updateTask({ server_file_id: fileId, total_parts: totalParts })
    } catch (err: any) {
      updateTask({ status: 'failed', error_message: `Failed initializing upload: ${err.message}` })
      processQueue()
      return
    }
  } else {
    // H-41: leave the selectable 'queued' state before awaiting the status query
    // so a concurrent processQueue() cannot pick this task for a second worker.
    updateTask({
      status: 'buffering',
      stage: 'Resuming upload session...',
    })
    // Session already exists: query backend upload status for one-click resumption!
    try {
      const statusRes = await api.getUploadStatus(fileId)
      uploadedPartsList = statusRes.uploaded_parts || []
      // H-48: exact bytes from uploaded part indices (last part is usually smaller than PART_SIZE)
      const completedBytes = uploadedPartsList.reduce((sum, idx) => {
        const start = idx * PART_SIZE
        return sum + Math.max(0, Math.min(PART_SIZE, file.size - start))
      }, 0)
      const pct = Math.min(99, Math.round((completedBytes / file.size) * 100))
      updateTask({
        uploaded_bytes: completedBytes,
        progress_percent: pct,
        current_part: (statusRes.next_part !== null ? statusRes.next_part + 1 : 1),
      })
    } catch {
      // If previous session not found, continue with clean parts list
    }
  }

  // 2. Upload missing parts sequentially
  for (let partIdx = 0; partIdx < totalParts; partIdx++) {
    const current = useUploadStore.getState().tasks.find((t) => t.id === taskId)
    if (!current || current.status === 'paused' || current.status === 'failed') {
      return
    }

    if (uploadedPartsList.includes(partIdx)) {
      continue // Part already in Telegram WAL; skip!
    }

    const byteOffset = partIdx * PART_SIZE
    const sliceEnd = Math.min(file.size, byteOffset + PART_SIZE)
    const chunkBlob = file.slice(byteOffset, sliceEnd)

    updateTask({
      status: 'uploading',
      stage: `Uploading Part ${partIdx + 1} of ${totalParts} to Telegram...`,
      current_part: partIdx + 1,
      total_parts: totalParts,
    })

    const formData = new FormData()
    formData.append('file_id', fileId)
    formData.append('part_index', String(partIdx))
    formData.append('total_parts', String(totalParts))
    formData.append('byte_offset', String(byteOffset))
    formData.append('part_sha256', '')
    formData.append('filename', file.name)
    formData.append('total_size', String(file.size))
    formData.append('chunk', chunkBlob, `${file.name}.part_${partIdx}`)

    let lastLoaded = 0
    let lastTime = Date.now()
    let lastUi = 0

    const { xhr, promise } = api.uploadChunk(formData, (loaded) => {
      const now = Date.now()
      if (now - lastUi < 200 && loaded < chunkBlob.size) return
      lastUi = now

      const timeDiff = (now - lastTime) / 1000
      let speed = 0
      if (timeDiff >= 0.2) {
        speed = Math.round((loaded - lastLoaded) / timeDiff)
        lastLoaded = loaded
        lastTime = now
      }

      const overallUploaded = byteOffset + loaded
      const pct = Math.min(99, Math.round((overallUploaded / file.size) * 100))
      const eta = speed > 0 ? Math.round((file.size - overallUploaded) / speed) : 0
      updateTask({
        uploaded_bytes: overallUploaded,
        progress_percent: pct,
        speed_bps: speed,
        eta_seconds: eta,
        current_part: partIdx + 1,
      })
    })

    activeUploads.set(taskId, xhr)

    try {
      await promise
      // H-39: identity guard — only clear this request's own registration.
      if (activeUploads.get(taskId) === xhr) {
        activeUploads.delete(taskId)
      }
      uploadedPartsList.push(partIdx)
    } catch (err: any) {
      const isCurrent = activeUploads.get(taskId) === xhr
      if (isCurrent) {
        activeUploads.delete(taskId)
      }
      const checkTask = useUploadStore.getState().tasks.find((t) => t.id === taskId)
      if (checkTask?.status === 'paused') {
        return
      }
      // H-39: this request was superseded (pause/resume/cancel/flood restart);
      // a stale rejection must not overwrite the state of the new worker.
      if (!isCurrent) {
        return
      }

      // H-42: mirror the single-part flood handling — a FLOOD_WAIT on a chunk
      // pauses the queue instead of failing the file; the next attempt resumes
      // from the committed parts.
      let floodWaitSec = 0
      try {
        const errJson = JSON.parse(xhr.responseText)
        if (errJson.flood_wait_seconds) {
          floodWaitSec = errJson.flood_wait_seconds
        } else if (typeof errJson.detail === 'string' && errJson.detail.includes('FLOOD_WAIT_')) {
          const match = errJson.detail.match(/FLOOD_WAIT_(\d+)/)
          if (match) floodWaitSec = parseInt(match[1], 10)
        }
      } catch {}
      if (!floodWaitSec) {
        const retryAfter = parseInt(xhr.getResponseHeader('Retry-After') || '0', 10)
        if (retryAfter > 0) floodWaitSec = retryAfter
      }

      if (xhr.status === 429 || floodWaitSec > 0) {
        const waitPenalty = (floodWaitSec || 10) + 2
        const cooldownUntil = Date.now() + waitPenalty * 1000
        updateTask({
          status: 'rate_limited',
          error_message: `Telegram FloodWait: pausing queue for ${waitPenalty}s to safeguard account...`,
          speed_bps: 0,
          eta_seconds: 0,
        })
        scheduleFloodWaitCooldown(cooldownUntil)
        return
      }

      updateTask({ status: 'failed', error_message: err.message || 'Chunk upload failed' })
      processQueue()
      return
    }
  }

  // 3. Complete multi-part upload
  updateTask({
    status: 'done',
    stage: 'Upload completed',
    progress_percent: 100,
    uploaded_bytes: file.size,
    speed_bps: 0,
    eta_seconds: 0,
    error_message: undefined,
  })

  // H-55: same album/folder association as the single-part path (album captured
  // at enqueue time, dropped-folder name as fallback)
  if (fileId) {
    associateUploadedFile(fileId, task)
  }

  useVaultStore.getState().fetchFiles()
  useVaultStore.getState().fetchAlbums()
  useVaultStore.getState().fetchMetrics()
  useVaultStore.getState().refreshCatalog()
  processQueue()
}

