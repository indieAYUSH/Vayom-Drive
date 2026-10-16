import React, { useEffect, useState } from 'react'
import { UploadCloud } from 'lucide-react'
import { useUploadStore } from '../../store/useUploadStore'

export const DropZoneOverlay: React.FC = () => {
  const { addFilesToUpload } = useUploadStore()
  const [isDragging, setIsDragging] = useState(false)

  useEffect(() => {
    let dragCounter = 0
    let isInternalDrag = false

    const handleDragStart = () => {
      // User is dragging an element already inside the browser window (e.g. thumbnail, text)
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

    const handleDrop = (e: DragEvent) => {
      e.preventDefault()
      const wasInternal = isInternalDrag
      isInternalDrag = false
      dragCounter = 0
      setIsDragging(false)

      if (wasInternal) return

      const files = e.dataTransfer?.files
      if (files && files.length > 0) {
        addFilesToUpload(files)
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
          <UploadCloud size={32} className="text-[#ffffff]" />
        </div>
        <h2 className="text-base font-bold uppercase tracking-widest text-[#ffffff] mb-2">
          Drop Files to Stage & Upload
        </h2>
        <p className="text-xs text-[#888888] max-w-md">
          Files &gt; 1.9 GB will be automatically partitioned into pointer-offset parts. Zero loss of original bytes.
        </p>
      </div>
    </div>
  )
}
