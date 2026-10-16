import React, { useEffect, useState } from 'react'
import { UploadCloud } from 'lucide-react'
import { useUploadStore } from '../../store/useUploadStore'

export const DropZoneOverlay: React.FC = () => {
  const { addFilesToUpload } = useUploadStore()
  const [isDragging, setIsDragging] = useState(false)

  useEffect(() => {
    let dragCounter = 0

    const handleDragEnter = (e: DragEvent) => {
      e.preventDefault()
      dragCounter++
      if (e.dataTransfer?.items && e.dataTransfer.items.length > 0) {
        setIsDragging(true)
      }
    }

    const handleDragLeave = (e: DragEvent) => {
      e.preventDefault()
      dragCounter--
      if (dragCounter === 0) {
        setIsDragging(false)
      }
    }

    const handleDragOver = (e: DragEvent) => {
      e.preventDefault()
    }

    const handleDrop = (e: DragEvent) => {
      e.preventDefault()
      dragCounter = 0
      setIsDragging(false)
      if (e.dataTransfer?.files && e.dataTransfer.files.length > 0) {
        addFilesToUpload(e.dataTransfer.files)
      }
    }

    window.addEventListener('dragenter', handleDragEnter)
    window.addEventListener('dragleave', handleDragLeave)
    window.addEventListener('dragover', handleDragOver)
    window.addEventListener('drop', handleDrop)

    return () => {
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
