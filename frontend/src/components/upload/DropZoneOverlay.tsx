import React, { useEffect, useState } from 'react'
import { UploadCloud, FolderUp } from 'lucide-react'
import { useUploadStore } from '../../store/useUploadStore'

async function extractFilesFromDataTransfer(dataTransfer: DataTransfer): Promise<File[]> {
  const files: File[] = []
  const items = dataTransfer.items

  if (items && items.length > 0 && typeof items[0].webkitGetAsEntry === 'function') {
    const traverseEntry = async (entry: any, path = ''): Promise<void> => {
      if (!entry) return
      if (entry.isFile) {
        return new Promise<void>((resolve) => {
          entry.file(
            (file: File) => {
              const relativePath = path ? `${path}/${file.name}` : file.name
              try {
                Object.defineProperty(file, 'webkitRelativePath', {
                  value: relativePath,
                  configurable: true,
                })
              } catch {}
              files.push(file)
              resolve()
            },
            () => resolve()
          )
        })
      } else if (entry.isDirectory) {
        const dirReader = entry.createReader()
        const readAllEntries = async (): Promise<any[]> => {
          const all: any[] = []
          let batch: any[] = await new Promise((res) => dirReader.readEntries(res, () => res([])))
          while (batch.length > 0) {
            all.push(...batch)
            batch = await new Promise((res) => dirReader.readEntries(res, () => res([])))
          }
          return all
        }

        const children = await readAllEntries()
        const newPath = path ? `${path}/${entry.name}` : entry.name
        for (const child of children) {
          await traverseEntry(child, newPath)
        }
      }
    }

    const promises: Promise<void>[] = []
    for (let i = 0; i < items.length; i++) {
      const entry = items[i].webkitGetAsEntry()
      if (entry) {
        promises.push(traverseEntry(entry))
      }
    }
    await Promise.all(promises)
    if (files.length > 0) return files
  }

  return Array.from(dataTransfer.files)
}

export const DropZoneOverlay: React.FC = () => {
  const { addFilesToUpload } = useUploadStore()
  const [isDragging, setIsDragging] = useState(false)

  useEffect(() => {
    let dragCounter = 0
    let isInternalDrag = false

    const handleDragStart = () => {
      isInternalDrag = true
    }

    const handleDragEnd = () => {
      isInternalDrag = false
      dragCounter = 0
      setIsDragging(false)
    }

    const isExternalFilesDrag = (e: DragEvent) => {
      if (isInternalDrag || !e.dataTransfer) return false
      const types = Array.from(e.dataTransfer.types || [])
      return types.includes('Files')
    }

    const handleDragEnter = (e: DragEvent) => {
      e.preventDefault()
      if (!isExternalFilesDrag(e)) return

      dragCounter++
      setIsDragging(true)
    }

    const handleDragLeave = (e: DragEvent) => {
      e.preventDefault()
      // H-37: leaving the window entirely (relatedTarget === null) ends the drag —
      // also clear a wedged internal-drag flag so external drops keep working
      if (e.relatedTarget === null) {
        isInternalDrag = false
        dragCounter = 0
        setIsDragging(false)
        return
      }
      if (isInternalDrag) return

      dragCounter--
      if (dragCounter <= 0) {
        dragCounter = 0
        setIsDragging(false)
      }
    }

    const handleDragOver = (e: DragEvent) => {
      e.preventDefault()
      if (!isExternalFilesDrag(e)) {
        if (e.dataTransfer) {
          e.dataTransfer.dropEffect = 'none'
        }
        return
      }
      if (e.dataTransfer) {
        e.dataTransfer.dropEffect = 'copy'
      }
    }

    const handleDrop = async (e: DragEvent) => {
      e.preventDefault()
      const wasInternal = isInternalDrag
      isInternalDrag = false
      dragCounter = 0
      setIsDragging(false)

      if (wasInternal || !e.dataTransfer) return

      const extracted = await extractFilesFromDataTransfer(e.dataTransfer)
      if (extracted.length > 0) {
        addFilesToUpload(extracted)
      }
    }

    window.addEventListener('dragstart', handleDragStart)
    window.addEventListener('dragend', handleDragEnd)
    window.addEventListener('dragenter', handleDragEnter)
    window.addEventListener('dragleave', handleDragLeave)
    window.addEventListener('dragover', handleDragOver)
    window.addEventListener('drop', handleDrop)

    return () => {
      window.removeEventListener('dragstart', handleDragStart)
      window.removeEventListener('dragend', handleDragEnd)
      window.removeEventListener('dragenter', handleDragEnter)
      window.removeEventListener('dragleave', handleDragLeave)
      window.removeEventListener('dragover', handleDragOver)
      window.removeEventListener('drop', handleDrop)
    }
  }, [addFilesToUpload])

  if (!isDragging) return null

  return (
    <div className="fixed inset-0 z-50 pointer-events-none flex items-center justify-center bg-[#000000] bg-opacity-90 select-none">
      <div className="w-[90vw] h-[85vh] border-2 border-dashed border-[#ffffff] flex flex-col items-center justify-center p-8 bg-[#080808] text-center font-mono">
        <div className="w-16 h-16 border border-[#ffffff] flex items-center justify-center mb-4 bg-[#141414]">
          <FolderUp size={32} className="text-[#ffffff]" />
        </div>
        <h2 className="text-base font-bold text-[#ffffff] uppercase tracking-wider mb-2">
          Drop Files or Entire Folders Here
        </h2>
        <p className="text-xs text-[#888888] max-w-md">
          Files and subdirectories are automatically scanned, encrypted, and streamed to your private Telegram vault
        </p>
        <div className="mt-6 flex items-center gap-4 text-[11px] text-[#555555]">
          <span>• 2000 MB Max Part Size</span>
          <span>• Zstandard & HEVC Policy</span>
          <span>• SHA-256 Verified</span>
        </div>
      </div>
    </div>
  )
}
