import React from 'react'
import { useVaultStore } from '../../store/useVaultStore'
import { Trash2, Download, FolderPlus, Tag, X } from 'lucide-react'
import { Button } from '../common/Button'

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
    selectedFiles.forEach((file) => {
      const url = file.preview_url || file.thumbnail_url
      if (url) {
        const a = document.createElement('a')
        a.href = url
        a.download = file.filename
        a.click()
      }
    })
  }

  const handleDelete = () => {
    moveToTrash(selectedIds)
  }

  return (
    <div className="fixed bottom-6 left-1/2 -translate-x-1/2 z-40 bg-[#000000] border-2 border-[#ffffff] text-[#ffffff] px-4 py-2.5 font-mono text-xs flex items-center gap-4 select-none shadow-none">
      <div className="font-semibold whitespace-nowrap">
        [ {selectedIds.length} {selectedIds.length === 1 ? 'item' : 'items'} selected ]
      </div>

      <div className="h-4 w-[1px] bg-[#333333]" />

      <div className="flex items-center gap-2">
        <Button
          variant="secondary"
          size="sm"
          onClick={handleBatchDownload}
          icon={<Download size={12} />}
        >
          Download
        </Button>

        <Button
          variant="secondary"
          size="sm"
          onClick={() => setCreateAlbumModalOpen(true)}
          icon={<FolderPlus size={12} />}
        >
          Add to Album
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
          className="p-1 text-[#888888] hover:text-[#ffffff] border border-transparent hover:border-[#333333] ml-1"
          title="Deselect all"
        >
          <X size={14} />
        </button>
      </div>
    </div>
  )
}
