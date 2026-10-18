import { create } from 'zustand'
import { UploadTask } from '../types/upload'
import { AlbumItem } from '../types/vault'
import { useVaultStore } from './useVaultStore'
import { api } from '../services/api'

interface UploadState {
  tasks: UploadTask[]
  isTrayOpen: boolean
  floodWaitSecondsRemaining: number

  selectedBackend: 'telegram' | 'nas' | 's3' | 'nextcloud'
  setSelectedBackend: (backend: 'telegram' | 'nas' | 's3' | 'nextcloud') => void
  setTrayOpen: (open: boolean) => void
  addFilesToUpload: (files: FileList | File[]) => void
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

// Anti-Ban Invariant 4: Limit upload concurrency to 3-4 parallel workers
const MAX_CONCURRENT_UPLOADS = 3
let floodWaitCooldownUntil = 0
let floodWaitTimeoutId: any = null
let floodWaitTickerId: any = null

// H-16: share one in-flight album-create promise per folder name so concurrent
// completions from the same directory drop cannot create duplicate albums
const albumCreatePromises = new Map<string, Promise<AlbumItem>>()

function findOrCreateAlbum(folderName: string): Promise<AlbumItem> {
  const key = folderName.toLowerCase()
  const existing = useVaultStore.getState().albums.find((a) => a.name.toLowerCase() === key)
  if (existing) {
    albumCreatePromises.delete(key)
    return Promise.resolve(existing)
  }
  let promise = albumCreatePromises.get(key)
  if (!promise) {
    promise = api.createAlbum(folderName).then((newAlbum) => {
      useVaultStore.getState().fetchAlbums()
      return newAlbum
    })
    promise.catch(() => albumCreatePromises.delete(key))
    albumCreatePromises.set(key, promise)
  }
  return promise
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
  const state = useUploadStore.getState()
  const hasRateLimited = state.tasks.some((t) => t.status === 'rate_limited')
  if (hasRateLimited) {
    state.tasks.forEach((t) => {
      if (t.status === 'rate_limited') {
        const xhr = activeUploads.get(t.id)
        if (xhr) {
          xhr.abort()
          activeUploads.delete(t.id)
        }
        const poll = activePollIntervals.get(t.id)
        if (poll) {
          clearInterval(poll)
          activePollIntervals.delete(t.id)
        }
      }
    })

    useUploadStore.setState((s) => ({
      tasks: s.tasks.map((t) =>
        t.status === 'rate_limited'
          ? { ...t, status: 'queued', error_message: undefined, speed_bps: 0 }
          : t
      ),
    }))
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
    if (task.total_parts > 1 || task.size_bytes > 100 * 1024 * 1024) {
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
  selectedBackend: 'telegram',

  setSelectedBackend: (backend) => set({ selectedBackend: backend }),
  setTrayOpen: (open) => set({ isTrayOpen: open }),

  addFilesToUpload: (fileInput) => {
    const fileArray = Array.from(fileInput)
    if (fileArray.length === 0) return

    const currentBackend = get().selectedBackend
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
    if (xhr) {
      xhr.abort()
      activeUploads.delete(id)
    }
    const pollInterval = activePollIntervals.get(id)
    if (pollInterval) {
      clearInterval(pollInterval)
      activePollIntervals.delete(id)
    }
    set((state) => ({
      tasks: state.tasks.map((t) => (t.id === id ? { ...t, status: 'paused', speed_bps: 0 } : t)),
    }))
    processQueue()
  },

  resumeTask: (id) => {
    const xhr = activeUploads.get(id)
    if (xhr) {
      xhr.abort()
      activeUploads.delete(id)
    }
    const pollInterval = activePollIntervals.get(id)
    if (pollInterval) {
      clearInterval(pollInterval)
      activePollIntervals.delete(id)
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
    if (xhr) {
      xhr.abort()
      activeUploads.delete(id)
    }
    const pollInterval = activePollIntervals.get(id)
    if (pollInterval) {
      clearInterval(pollInterval)
      activePollIntervals.delete(id)
    }

    // H-6: Call cleanupIncompleteUpload to purge orphan Telegram message parts
    if (task?.server_file_id) {
      api.cleanupIncompleteUpload(task.server_file_id).catch(() => {})
    }

    set((state) => ({
      tasks: state.tasks.filter((t) => t.id !== id),
    }))
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

  const cleanupPolling = () => {
    const interval = activePollIntervals.get(taskId)
    if (interval) {
      clearInterval(interval)
      activePollIntervals.delete(taskId)
    }
  }

  // Poll real-time MTProto upload progress from backend every 300ms with concurrency guard
  let isPolling = false
  const pollInterval = setInterval(async () => {
    if (isPolling) return
    isPolling = true
    try {
      const controller = new AbortController()
      const timeoutId = setTimeout(() => controller.abort(), 2500)
      const res = await fetch(`/api/v1/upload/${encodeURIComponent(taskId)}/progress`, {
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
          const xhr = activeUploads.get(taskId)
          if (xhr) {
            xhr.abort()
            activeUploads.delete(taskId)
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
    activeUploads.delete(taskId)
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
        const currentAlbumId = useVaultStore.getState().selectedAlbumId
        if (json.file_id && currentAlbumId) {
          api.addFileToAlbum(currentAlbumId, json.file_id).catch(() => {})
        } else if (json.file_id && (task.file as any).webkitRelativePath) {
          const rel = (task.file as any).webkitRelativePath as string
          const parts = rel.split('/')
          if (parts.length > 1) {
            // H-16: concurrent completions from one directory drop share a single create
            findOrCreateAlbum(parts[0])
              .then((album) => api.addFileToAlbum(album.id, json.file_id).catch(() => {}))
              .catch(() => {})
          }
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
    activeUploads.delete(taskId)
    updateTask({
      status: 'failed',
      error_message: 'Network connection error during MTProto upload',
      speed_bps: 0,
    })
    processQueue()
  }

  xhr.onabort = () => {
    cleanupPolling()
    activeUploads.delete(taskId)
    processQueue()
  }

  const formData = new FormData()
  formData.append('file', task.file)
  formData.append('task_id', taskId)

  xhr.open('POST', `/api/v1/upload?task_id=${encodeURIComponent(taskId)}`)
  xhr.setRequestHeader('X-Task-ID', taskId)
  if (task.storage_backend) {
    xhr.setRequestHeader('X-Storage-Backend', task.storage_backend)
  }
  xhr.send(formData)
}

// H-5: Resumable chunked upload worker for multi-part files
async function executeChunkedUpload(taskId: string) {
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
      })
      fileId = initRes.file_id
      updateTask({ server_file_id: fileId, total_parts: totalParts })
    } catch (err: any) {
      updateTask({ status: 'failed', error_message: `Failed initializing upload: ${err.message}` })
      processQueue()
      return
    }
  } else {
    // Session already exists: query backend upload status for one-click resumption!
    try {
      const statusRes = await api.getUploadStatus(fileId)
      uploadedPartsList = statusRes.uploaded_parts || []
      const completedBytes = uploadedPartsList.length * Math.min(PART_SIZE, file.size / totalParts)
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
      activeUploads.delete(taskId)
      uploadedPartsList.push(partIdx)
    } catch (err: any) {
      activeUploads.delete(taskId)
      const checkTask = useUploadStore.getState().tasks.find((t) => t.id === taskId)
      if (checkTask?.status === 'paused') {
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

  // Auto-associate with current folder
  const currentAlbumId = useVaultStore.getState().selectedAlbumId
  if (fileId && currentAlbumId) {
    api.addFileToAlbum(currentAlbumId, fileId).catch(() => {})
  }

  useVaultStore.getState().fetchFiles()
  useVaultStore.getState().fetchAlbums()
  useVaultStore.getState().fetchMetrics()
  useVaultStore.getState().refreshCatalog()
  processQueue()
}

