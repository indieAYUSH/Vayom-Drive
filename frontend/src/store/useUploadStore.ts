import { create } from 'zustand'
import { UploadTask, UploadStatus } from '../types/upload'
import { useVaultStore } from './useVaultStore'
import { FileItem } from '../types/vault'

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
      isTrayOpen: true
    }))

    // Start simulation pipeline for added tasks
    newTasks.forEach((task) => {
      simulateUploadPipeline(task.id)
    })
  },

  pauseTask: (id) => set((state) => ({
    tasks: state.tasks.map((t) => t.id === id ? { ...t, status: 'paused' } : t)
  })),

  resumeTask: (id) => {
    set((state) => ({
      tasks: state.tasks.map((t) => t.id === id ? { ...t, status: 'uploading' } : t)
    }))
    simulateUploadPipeline(id)
  },

  cancelTask: (id) => set((state) => ({
    tasks: state.tasks.filter((t) => t.id !== id)
  })),

  clearCompleted: () => set((state) => ({
    tasks: state.tasks.filter((t) => t.status !== 'done')
  }))
}))

// Realistic multi-stage upload simulator demonstrating size-policy, splitting, and Telegram commit
function simulateUploadPipeline(taskId: string) {
  const updateTask = (updates: Partial<UploadTask>) => {
    useUploadStore.setState((state) => ({
      tasks: state.tasks.map((t) => t.id === taskId ? { ...t, ...updates } : t)
    }))
  }

  const task = useUploadStore.getState().tasks.find((t) => t.id === taskId)
  if (!task) return

  // Stage 1: Hashing & Deduplication
  updateTask({ status: 'hashing', progress_percent: 10 })

  setTimeout(() => {
    const current = useUploadStore.getState().tasks.find((t) => t.id === taskId)
    if (!current || current.status === 'paused') return

    // Stage 2: Metadata & Size Policy
    const isBig = current.size_bytes > 1900000000
    updateTask({ 
      status: isBig ? 'splitting' : 'analyzing', 
      progress_percent: 25 
    })

    setTimeout(() => {
      const stageTask = useUploadStore.getState().tasks.find((t) => t.id === taskId)
      if (!stageTask || stageTask.status === 'paused') return

      updateTask({ status: 'uploading', progress_percent: 30 })

      let progress = 30
      const interval = setInterval(() => {
        const active = useUploadStore.getState().tasks.find((t) => t.id === taskId)
        if (!active || active.status === 'paused') {
          clearInterval(interval)
          return
        }

        progress += Math.floor(Math.random() * 15) + 10
        if (progress >= 100) {
          progress = 100
          clearInterval(interval)
          updateTask({ 
            status: 'verifying', 
            progress_percent: 100,
            uploaded_bytes: active.size_bytes,
            speed_bps: 48500000 
          })

          setTimeout(() => {
            updateTask({ status: 'done', speed_bps: 0, eta_seconds: 0 })

            // Inject newly uploaded item into vault store
            const newFile: FileItem = {
              id: `item-${Date.now()}`,
              filename: active.filename,
              mime_type: active.file.type || 'application/octet-stream',
              file_category: getCategoryFromMime(active.file.type, active.filename),
              size_bytes: active.size_bytes,
              sha256: Math.random().toString(36).substring(2) + Math.random().toString(36).substring(2),
              is_compressed: active.size_bytes > 50000000,
              is_lossy_video: false,
              is_split: active.total_parts > 1,
              total_parts: active.total_parts,
              parts: Array.from({ length: active.total_parts }).map((_, i) => ({
                part_index: i,
                offset: i * 1900000000,
                size: Math.min(1900000000, active.size_bytes - i * 1900000000),
                telegram_message_id: Math.floor(Math.random() * 9000) + 1000,
                sha256: Math.random().toString(36).substring(2)
              })),
              thumbnail_url: active.file.type.startsWith('image/') 
                ? URL.createObjectURL(active.file) 
                : undefined,
              tags: ['uploaded', 'telegram-stored'],
              albums: [],
              created_at: new Date().toISOString(),
              is_deleted: false,
            }

            useVaultStore.getState().addFiles([newFile])
          }, 600)
        } else {
          const uploaded = Math.floor((progress / 100) * active.size_bytes)
          updateTask({
            progress_percent: progress,
            uploaded_bytes: uploaded,
            speed_bps: 52400000 + Math.floor(Math.random() * 5000000), // ~52 MB/s
            eta_seconds: Math.max(1, Math.round(((active.size_bytes - uploaded) / 52400000)))
          })
        }
      }, 300)
    }, 500)
  }, 400)
}

function getCategoryFromMime(mime: string, name: string): FileItem['file_category'] {
  if (mime.startsWith('image/') || /\.(jpg|jpeg|png|webp|gif|arw|cr2|nef|dng|tiff)$/i.test(name)) return 'image'
  if (mime.startsWith('video/') || /\.(mp4|mkv|mov|avi|webm)$/i.test(name)) return 'video'
  if (/\.(zip|tar|gz|zst|xz|7z|rar)$/i.test(name)) return 'archive'
  if (/\.(pdf|doc|docx|txt|md|csv|xlsx)$/i.test(name)) return 'document'
  return 'other'
}
