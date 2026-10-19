import React from 'react'
import { useVaultStore } from '../../store/useVaultStore'
import { Trash2, Download, FolderPlus, Tag, X } from 'lucide-react'
import { Button } from '../common/Button'
import { apiUrl } from '../../utils/apiUrl'

export const BulkActionBar: React.FC = () => {
  const { 
    selectedIds, 
    clearSelection, 
    moveToTrash, 
    files, 
    setCreateAlbumModalOpen 
  } = useVaultStore()

  if (selectedIds.length === 0) return null

  const handleBatchDownload = () => {
    const selectedFiles = files.filter((f) => selectedIds.includes(f.id))
    // H-33: fall back to the download endpoint instead of silently skipping items,
    // and stagger the anchor clicks so the browser doesn't throttle parallel downloads
    const downloads = selectedFiles.map((file) => ({
      url: apiUrl(`/api/v1/files/${file.id}/download`),
      filename: file.filename,
    }))
    downloads.forEach((d, index) => {
      setTimeout(() => {
        const a = document.createElement('a')
        a.href = d.url
        a.download = d.filename
        a.click()
      }, index * 250)
    })
  }

  const handleDelete = () => {
    // H-17: surface failures instead of an unhandled rejection
    moveToTrash(selectedIds).catch((err: any) => alert(err?.message || 'Failed to move items to trash'))
  }

  return (
    <div className="fixed bottom-16 sm:bottom-6 left-1/2 -translate-x-1/2 z-40 bg-[#000000] border-2 border-[#ffffff] text-[#ffffff] px-3 sm:px-4 py-2 sm:py-2.5 text-xs flex flex-wrap sm:flex-nowrap items-center justify-between sm:justify-center gap-2 sm:gap-4 select-none shadow-none max-w-[calc(100vw-1rem)] w-auto">
      <div className="font-mono font-medium whitespace-nowrap text-xs text-[#ffffff]">
        [ {selectedIds.length} {selectedIds.length === 1 ? 'item' : 'items'} ]
      </div>

      <div className="hidden sm:block h-4 w-[1px] bg-[#333333]" />

      <div className="flex items-center gap-1.5 sm:gap-2">
        <Button
          variant="secondary"
          size="sm"
          onClick={handleBatchDownload}
          icon={<Download size={12} />}
        >
          <span className="hidden sm:inline">Download</span>
          <span className="sm:hidden">Get</span>
        </Button>

        <Button
          variant="secondary"
          size="sm"
          onClick={() => setCreateAlbumModalOpen(true)}
          icon={<FolderPlus size={12} />}
        >
          <span className="hidden sm:inline">Add to Folder</span>
          <span className="sm:hidden">Folder</span>
        </Button>

        <Button
          variant="danger"
          size="sm"
          onClick={handleDelete}
          icon={<Trash2 size={12} />}
        >
          Trash
        </Button>

        <button
          onClick={clearSelection}
          className="p-1 text-[#888888] hover:text-[#ffffff] border border-transparent hover:border-[#333333] ml-0.5"
          title="Deselect all"
        >
          <X size={14} />
        </button>
      </div>
    </div>
  )
}
