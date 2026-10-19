import React, { useEffect, useState } from 'react'
import { FolderUp, Archive, Folder, Loader2, X } from 'lucide-react'
import { useUploadStore } from '../../store/useUploadStore'
import { Button } from '../common/Button'
import JSZip from 'jszip'

async function extractFilesFromDataTransfer(dataTransfer: DataTransfer): Promise<File[]> {
  const files: File[] = []
  const items = dataTransfer.items

  // 1. Try Modern File System Access API (getAsFileSystemHandle)
  if (items && items.length > 0 && typeof (items[0] as any).getAsFileSystemHandle === 'function') {
    try {
      async function walkHandle(handle: any, currentPath = ''): Promise<void> {
        if (!handle) return
        if (handle.kind === 'file') {
          const file = await handle.getFile()
          const rel = currentPath ? `${currentPath}/${file.name}` : file.name
          ;(file as any).customRelativePath = rel
          try {
            Object.defineProperty(file, 'webkitRelativePath', { value: rel, configurable: true })
          } catch {}
          files.push(file)
        } else if (handle.kind === 'directory') {
          const dirPath = currentPath ? `${currentPath}/${handle.name}` : handle.name
          for await (const entry of handle.values()) {
            await walkHandle(entry, dirPath)
          }
        }
      }

      const promises: Promise<void>[] = []
      for (let i = 0; i < items.length; i++) {
        const item = items[i]
        if (item.kind === 'file') {
          const handle = await (item as any).getAsFileSystemHandle()
          if (handle) {
            promises.push(walkHandle(handle, handle.kind === 'directory' ? handle.name : ''))
          }
        }
      }
      await Promise.all(promises)
      if (files.length > 0) return files
    } catch (e) {
      console.warn('FileSystemHandle traversal failed, falling back to webkitGetAsEntry:', e)
    }
  }

  // 2. Fallback to WebKit / Blink / Gecko Entry API (webkitGetAsEntry)
  if (items && items.length > 0) {
    const traverseEntry = async (entry: any, path = ''): Promise<void> => {
      if (!entry) return
      if (entry.isFile) {
        return new Promise<void>((resolve) => {
          entry.file(
            (file: File) => {
              const relativePath = path ? `${path}/${file.name}` : file.name
              ;(file as any).customRelativePath = relativePath
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
          while (true) {
            const batch: any[] = await new Promise((res) => dirReader.readEntries(res, () => res([])))
            if (!batch || batch.length === 0) break
            all.push(...batch)
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
      const item = items[i]
      const entry = typeof item.webkitGetAsEntry === 'function' 
        ? item.webkitGetAsEntry() 
        : typeof (item as any).getAsEntry === 'function' 
        ? (item as any).getAsEntry() 
        : null
      if (entry) {
        promises.push(traverseEntry(entry))
      }
    }
    await Promise.all(promises)
    if (files.length > 0) return files
  }

  // 3. Fallback to standard dataTransfer.files
  const rawFiles = Array.from(dataTransfer.files)
  for (const f of rawFiles) {
    if (!(f as any).customRelativePath) {
      ;(f as any).customRelativePath = f.webkitRelativePath || f.name
    }
  }
  return rawFiles
}

export const DropZoneOverlay: React.FC = () => {
  const { addFilesToUpload } = useUploadStore()
  const [isDragging, setIsDragging] = useState(false)
  const [folderPrompt, setFolderPrompt] = useState<{
    folderName: string
    files: File[]
  } | null>(null)
  const [isZipping, setIsZipping] = useState(false)

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
      if (extracted.length === 0) return

      // Detect if files belong to a dropped folder
      const rootFolders = Array.from(
        new Set(
          extracted
            .map((f) => {
              const rel = (f as any).customRelativePath || f.webkitRelativePath || ''
              const parts = rel.split('/')
              return parts.length > 1 ? parts[0] : null
            })
            .filter(Boolean)
        )
      ) as string[]

      if (rootFolders.length === 1) {
        // Single root folder dropped -> offer option to Upload as Folder or Compress as .ZIP
        setFolderPrompt({
          folderName: rootFolders[0],
          files: extracted,
        })
      } else {
        // Individual files or multi-folder drop -> upload directly
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

  const handleUploadAsFolder = () => {
    if (!folderPrompt) return
    addFilesToUpload(folderPrompt.files)
    setFolderPrompt(null)
  }

  const handleCompressToZipAndUpload = async () => {
    if (!folderPrompt) return
    setIsZipping(true)
    try {
      const zip = new JSZip()
      for (const file of folderPrompt.files) {
        const rel = (file as any).customRelativePath || file.webkitRelativePath || file.name
        // Store path inside zip relative to root folder
        const parts = rel.split('/')
        const internalPath = parts.length > 1 ? parts.slice(1).join('/') : file.name
        zip.file(internalPath, file)
      }

      const zipBlob = await zip.generateAsync({
        type: 'blob',
        compression: 'DEFLATE',
        compressionOptions: { level: 6 },
      })

      const zipFile = new File([zipBlob], `${folderPrompt.folderName}.zip`, {
        type: 'application/zip',
        lastModified: Date.now(),
      })

      addFilesToUpload([zipFile])
      setFolderPrompt(null)
    } catch (err) {
      console.error('Failed to create zip file:', err)
      alert('Failed to compress folder into zip. Falling back to folder upload.')
      addFilesToUpload(folderPrompt.files)
      setFolderPrompt(null)
    } finally {
      setIsZipping(false)
    }
  }

  return (
    <>
      {/* Dragging Fullscreen Overlay */}
      {isDragging && (
        <div className="fixed inset-0 z-50 pointer-events-none flex items-center justify-center bg-[#000000] bg-opacity-90 select-none">
          <div className="w-[90vw] h-[85vh] border-2 border-dashed border-[#ffffff] flex flex-col items-center justify-center p-8 bg-[#080808] text-center">
            <div className="w-16 h-16 border border-[#ffffff] flex items-center justify-center mb-4 bg-[#141414]">
              <FolderUp size={32} className="text-[#ffffff]" />
            </div>
            <h2 className="text-base font-semibold text-[#ffffff] tracking-normal mb-2">
              Drop Files or Entire Folders Here
            </h2>
            <p className="text-xs text-[#a3a3a3] max-w-md leading-relaxed">
              Files and directories will be imported, organized, and uploaded to your storage vault
            </p>
            <div className="mt-6 flex items-center gap-4 text-xs text-[#888888] font-mono">
              <span>• Auto-organize into Albums</span>
              <span>• Optional .ZIP Compression</span>
              <span>• Zero-Knowledge Encryption</span>
            </div>
          </div>
        </div>
      )}

      {/* Folder Drop Decision Modal */}
      {folderPrompt && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-[#000000]/80 backdrop-blur-sm p-4 font-sans select-none">
          <div className="bg-[#0c0c0c] border border-[#282828] max-w-md w-full p-6 flex flex-col gap-4 shadow-2xl">
            <div className="flex items-center justify-between border-b border-[#202020] pb-3">
              <div className="flex items-center gap-2 text-[#ffffff]">
                <Folder size={18} className="text-[#ffffff]" />
                <span className="font-semibold text-sm">Folder Detected</span>
              </div>
              {!isZipping && (
                <button
                  onClick={() => setFolderPrompt(null)}
                  className="text-[#666666] hover:text-[#ffffff] transition-colors"
                >
                  <X size={16} />
                </button>
              )}
            </div>

            <div className="flex flex-col gap-1">
              <p className="text-xs text-[#d4d4d4]">
                You dropped folder <span className="text-[#ffffff] font-semibold font-mono">"{folderPrompt.folderName}"</span> containing{' '}
                <span className="text-[#ffffff] font-semibold font-mono">{folderPrompt.files.length}</span> files.
              </p>
              <p className="text-[11px] text-[#888888]">
                How would you like to import this folder?
              </p>
            </div>

            <div className="flex flex-col gap-2.5 pt-2">
              <button
                onClick={handleUploadAsFolder}
                disabled={isZipping}
                className="w-full flex items-center justify-between px-4 py-3 bg-[#141414] border border-[#282828] hover:border-[#ffffff] text-left transition-colors group"
              >
                <div className="flex items-center gap-3">
                  <div className="w-8 h-8 rounded bg-[#1c1c1c] border border-[#333333] flex items-center justify-center text-[#ffffff]">
                    <Folder size={16} />
                  </div>
                  <div>
                    <div className="text-xs font-semibold text-[#f0f0f0] group-hover:text-[#ffffff]">
                      Upload as Folder
                    </div>
                    <div className="text-[10px] text-[#777777]">
                      Creates album "{folderPrompt.folderName}" with individual files
                    </div>
                  </div>
                </div>
                <span className="text-[10px] font-mono text-[#888888] uppercase">Album</span>
              </button>

              <button
                onClick={handleCompressToZipAndUpload}
                disabled={isZipping}
                className="w-full flex items-center justify-between px-4 py-3 bg-[#141414] border border-[#282828] hover:border-[#ffffff] text-left transition-colors group"
              >
                <div className="flex items-center gap-3">
                  <div className="w-8 h-8 rounded bg-[#1c1c1c] border border-[#333333] flex items-center justify-center text-[#ffffff]">
                    {isZipping ? <Loader2 size={16} className="animate-spin text-[#ffffff]" /> : <Archive size={16} />}
                  </div>
                  <div>
                    <div className="text-xs font-semibold text-[#f0f0f0] group-hover:text-[#ffffff]">
                      {isZipping ? 'Compressing to .ZIP...' : 'Compress to .ZIP Archive'}
                    </div>
                    <div className="text-[10px] text-[#777777]">
                      Packages all files into a single "{folderPrompt.folderName}.zip"
                    </div>
                  </div>
                </div>
                <span className="text-[10px] font-mono text-[#888888] uppercase">Archive</span>
              </button>
            </div>

            <div className="flex justify-end pt-2 border-t border-[#202020]">
              <Button
                variant="ghost"
                size="sm"
                onClick={() => setFolderPrompt(null)}
                disabled={isZipping}
              >
                Cancel
              </Button>
            </div>
          </div>
        </div>
      )}
    </>
  )
}
