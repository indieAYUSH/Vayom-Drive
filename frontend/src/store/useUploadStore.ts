import { create } from 'zustand'
import { UploadTask } from '../types/upload'
import { useVaultStore } from './useVaultStore'
import { api } from '../services/api'

interface UploadState {
  tasks: UploadTask[]
  isTrayOpen: boolean
  isDraggingOver: boolean

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

export const useUploadStore = create<UploadState>((set, get) => ({
  tasks: [],
  isTrayOpen: true,
  isDraggingOver: false,

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

    // Start real uploads
    newTasks.forEach((task) => {
      executeRealUpload(task.id)
    })
  },

  pauseTask: (id) => {
    const xhr = activeUploads.get(id)
    if (xhr) {
      xhr.abort()
      activeUploads.delete(id)
    }
    set((state) => ({
      tasks: state.tasks.map((t) => (t.id === id ? { ...t, status: 'paused', speed_bps: 0 } : t)),
    }))
  },

  resumeTask: (id) => {
    set((state) => ({
      tasks: state.tasks.map((t) => (t.id === id ? { ...t, status: 'queued' } : t)),
    }))
    executeRealUpload(id)
  },

  cancelTask: (id) => {
    const xhr = activeUploads.get(id)
    if (xhr) {
      xhr.abort()
      activeUploads.delete(id)
    }
    set((state) => ({
      tasks: state.tasks.filter((t) => t.id !== id),
    }))
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

  updateTask({ status: 'uploading', progress_percent: 0 })

  const xhr = new XMLHttpRequest()
  activeUploads.set(taskId, xhr)

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

      const percent = Math.min(99, Math.round((event.loaded / event.total) * 100))
      const remainingBytes = event.total - event.loaded
      const eta = speed > 0 ? Math.round(remainingBytes / speed) : 0

      updateTask({
        uploaded_bytes: event.loaded,
        progress_percent: percent,
        speed_bps: speed,
        eta_seconds: eta,
        status: percent >= 98 ? 'verifying' : 'uploading',
      })
    }
  }

  xhr.onload = () => {
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
    } else {
      updateTask({
        status: 'failed',
        error_message: `Upload failed: ${xhr.status} ${xhr.statusText}`,
        speed_bps: 0,
      })
    }
  }

  xhr.onerror = () => {
    activeUploads.delete(taskId)
    updateTask({
      status: 'failed',
      error_message: 'Network connection error during MTProto upload',
      speed_bps: 0,
    })
  }

  xhr.onabort = () => {
    activeUploads.delete(taskId)
  }

  const formData = new FormData()
  formData.append('file', task.file)

  xhr.open('POST', '/api/v1/upload')
  xhr.send(formData)
}
