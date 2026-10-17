import { create } from 'zustand'
import { UploadTask } from '../types/upload'
import { useVaultStore } from './useVaultStore'
import { api } from '../services/api'

interface UploadState {
  tasks: UploadTask[]
  isTrayOpen: boolean
  isDraggingOver: boolean
  floodWaitSecondsRemaining: number

  setTrayOpen: (open: boolean) => void
  setDraggingOver: (dragging: boolean) => void
  addFilesToUpload: (files: FileList | File[]) => void
  pauseTask: (id: string) => void
  resumeTask: (id: string) => void
  cancelTask: (id: string) => void
  clearCompleted: () => void
}

// Map of active XMLHttpRequest instances for pause/cancellation
const activeUploads = new Map<string, XMLHttpRequest>()
const activePollIntervals = new Map<string, any>()

// Anti-Ban Invariant 4: Limit upload concurrency to 3-4 parallel workers
const MAX_CONCURRENT_UPLOADS = 3
let floodWaitCooldownUntil = 0
let floodWaitTimeoutId: any = null
let floodWaitTickerId: any = null

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

function processQueue() {
  const now = Date.now()
  if (floodWaitCooldownUntil > now) {
    updateFloodWaitTicker()
    if (!floodWaitTimeoutId) {
      floodWaitTimeoutId = setTimeout(() => {
        floodWaitTimeoutId = null
        processQueue()
      }, floodWaitCooldownUntil - now)
    }
    return
  }

  const state = useUploadStore.getState()
  const activeCount = state.tasks.filter(
    (t) => t.status === 'uploading' || t.status === 'verifying'
  ).length

  const availableSlots = MAX_CONCURRENT_UPLOADS - activeCount
  if (availableSlots <= 0) return

  const queuedTasks = state.tasks.filter((t) => t.status === 'queued').slice(0, availableSlots)
  for (const task of queuedTasks) {
    executeRealUpload(task.id)
  }
}

export const useUploadStore = create<UploadState>((set, get) => ({
  tasks: [],
  isTrayOpen: true,
  isDraggingOver: false,
  floodWaitSecondsRemaining: 0,

  setTrayOpen: (open) => set({ isTrayOpen: open }),
  setDraggingOver: (dragging) => set({ isDraggingOver: dragging }),

  addFilesToUpload: (fileInput) => {
    const fileArray = Array.from(fileInput)
    if (fileArray.length === 0) return

    const newTasks: UploadTask[] = fileArray.map((file) => {
      const isLarge = file.size > 1900000000
      const totalParts = isLarge ? Math.ceil(file.size / 1900000000) : 1
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
    set((state) => ({
      tasks: state.tasks.map((t) => (t.id === id ? { ...t, status: 'queued' } : t)),
    }))
    processQueue()
  },

  cancelTask: (id) => {
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
      tasks: state.tasks.filter((t) => t.id !== id),
    }))
    processQueue()
  },

  clearCompleted: () =>
    set((state) => ({
      tasks: state.tasks.filter((t) => t.status !== 'done'),
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
    status: 'uploading',
    progress_percent: 0,
    uploaded_bytes: 0,
    speed_bps: 0,
    eta_seconds: 0
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

  // Poll real-time MTProto upload progress from backend every 250ms
  const pollInterval = setInterval(async () => {
    try {
      const res = await fetch(`/api/v1/upload/${encodeURIComponent(taskId)}/progress`)
      if (!res.ok) return
      const data = await res.json()
      if (data && data.status && data.status !== 'unknown') {
        const currentTask = useUploadStore.getState().tasks.find((t) => t.id === taskId)
        if (!currentTask || currentTask.status === 'paused' || currentTask.status === 'done' || currentTask.status === 'failed') {
          cleanupPolling()
          return
        }

        const updates: Partial<UploadTask> = {}
        if (typeof data.uploaded_bytes === 'number' && data.uploaded_bytes > 0) {
          updates.uploaded_bytes = data.uploaded_bytes
        }
        if (typeof data.progress_percent === 'number') {
          updates.progress_percent = data.progress_percent
        }
        if (typeof data.speed_bps === 'number') {
          updates.speed_bps = data.speed_bps
        }
        if (typeof data.eta_seconds === 'number') {
          updates.eta_seconds = data.eta_seconds
        }
        if (data.status === 'rate_limited') {
          const waitSec = data.flood_wait_seconds || 10
          floodWaitCooldownUntil = Date.now() + (waitSec + 2) * 1000
          updateFloodWaitTicker()
          updates.status = 'rate_limited'
          updates.error_message = data.stage || `Telegram Rate-Limit active: Pausing for ${Math.ceil(waitSec)}s to safeguard account...`
          updates.speed_bps = 0
        } else if (data.status === 'verifying') {
          updates.status = 'verifying'
          updates.error_message = undefined
        } else if (data.status === 'uploading') {
          updates.status = 'uploading'
          updates.error_message = undefined
        } else if (data.status === 'done') {
          updates.status = 'done'
          updates.error_message = undefined
          cleanupPolling()
        }
        if (data.current_part) updates.current_part = data.current_part
        if (data.total_parts) updates.total_parts = data.total_parts

        updateTask(updates)
      }
    } catch {}
  }, 250)

  activePollIntervals.set(taskId, pollInterval)

  // Track initial local buffer transfer without prematurely setting 100% or 'verifying'
  let lastLoaded = 0
  let lastTime = Date.now()

  xhr.upload.onprogress = (event) => {
    if (event.lengthComputable) {
      const now = Date.now()
      const timeDiff = (now - lastTime) / 1000
      let speed = 0
      if (timeDiff >= 0.2) {
        speed = Math.round((event.loaded - lastLoaded) / timeDiff)
        lastLoaded = event.loaded
        lastTime = now
      }

      const currentTask = useUploadStore.getState().tasks.find((t) => t.id === taskId)
      // Only display preliminary buffer progress if backend MTProto upload hasn't started reporting bytes
      if (currentTask && (!currentTask.uploaded_bytes || currentTask.uploaded_bytes === 0)) {
        const initialBufferPct = Math.min(5, Math.round((event.loaded / event.total) * 5))
        updateTask({
          progress_percent: initialBufferPct,
          speed_bps: speed,
          status: 'uploading',
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
        progress_percent: 100,
        uploaded_bytes: task.size_bytes,
        speed_bps: 0,
        eta_seconds: 0,
      })

      // Auto-associate with current folder or dropped directory structure
      try {
        const json = JSON.parse(xhr.responseText)
        const currentAlbumId = useVaultStore.getState().selectedAlbumId
        if (json.file_id && currentAlbumId) {
          api.addFileToAlbum(currentAlbumId, json.file_id).catch(() => {})
        } else if (json.file_id && (task.file as any).webkitRelativePath) {
          const rel = (task.file as any).webkitRelativePath as string
          const parts = rel.split('/')
          if (parts.length > 1) {
            const folderName = parts[0]
            const albums = useVaultStore.getState().albums
            const existing = albums.find((a) => a.name.toLowerCase() === folderName.toLowerCase())
            if (existing) {
              api.addFileToAlbum(existing.id, json.file_id).catch(() => {})
            } else {
              api.createAlbum(folderName).then((newAlb) => {
                useVaultStore.getState().fetchAlbums()
                api.addFileToAlbum(newAlb.id, json.file_id).catch(() => {})
              }).catch(() => {})
            }
          }
        }
      } catch {}

      // Refresh files catalog and metrics in vault store
      useVaultStore.getState().fetchFiles()
      useVaultStore.getState().fetchAlbums()
      useVaultStore.getState().fetchMetrics()
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
        floodWaitCooldownUntil = Date.now() + waitPenalty * 1000
        updateFloodWaitTicker()
        updateTask({
          status: 'rate_limited',
          error_message: `Telegram FloodWait: pausing queue for ${waitPenalty}s to safeguard account...`,
          speed_bps: 0,
        })
        if (floodWaitTimeoutId) clearTimeout(floodWaitTimeoutId)
        floodWaitTimeoutId = setTimeout(() => {
          floodWaitTimeoutId = null
          updateTask({
            status: 'queued',
            error_message: undefined,
          })
          processQueue()
        }, waitPenalty * 1000)
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
  xhr.send(formData)
}
