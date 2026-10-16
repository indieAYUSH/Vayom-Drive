import React from 'react'
import { useVaultStore } from '../store/useVaultStore'
import { Trash2, RotateCcw } from 'lucide-react'
import { Button } from '../components/common/Button'
import { Badge } from '../components/common/Badge'

export const TrashView: React.FC = () => {
  const { files, restoreFromTrash, purgeFiles } = useVaultStore()

  const deletedFiles = files.filter((f) => f.is_deleted)

  const handleRestoreAll = () => {
    restoreFromTrash(deletedFiles.map((f) => f.id))
  }

  const handleEmptyTrash = () => {
    if (window.confirm('Permanently purge all items from trash? This cannot be undone.')) {
      purgeFiles(deletedFiles.map((f) => f.id))
    }
  }

  const formatBytes = (bytes: number) => {
    if (bytes >= 1024 * 1024 * 1024) return `${(bytes / (1024 * 1024 * 1024)).toFixed(2)} GB`
    return `${(bytes / (1024 * 1024)).toFixed(1)} MB`
  }

  return (
    <div className="flex-1 overflow-y-auto p-6 bg-[#000000] select-none font-mono">
      {/* Top Header */}
      <div className="flex items-center justify-between pb-6 border-b border-[#282828] mb-6">
        <div>
          <h1 className="text-base font-bold uppercase tracking-wider text-[#ef4444] flex items-center gap-2">
            <Trash2 size={16} /> Trash & 30-Day Retention
          </h1>
          <p className="text-xs text-[#777777] mt-1">
            Items remain in trash for 30 days before automated permanent purge.
          </p>
        </div>

        {deletedFiles.length > 0 && (
          <div className="flex items-center gap-2">
            <Button
              variant="outline"
              size="sm"
              onClick={handleRestoreAll}
              icon={<RotateCcw size={12} />}
            >
              Restore All
            </Button>
            <Button
              variant="danger"
              size="sm"
              onClick={handleEmptyTrash}
              icon={<Trash2 size={12} />}
            >
              Empty Trash
            </Button>
          </div>
        )}
      </div>

      {deletedFiles.length === 0 ? (
        <div className="flex flex-col items-center justify-center p-16 text-center text-[#555555]">
          <div className="w-12 h-12 border border-[#222222] flex items-center justify-center mb-3 text-[#444444]">
            ✓
          </div>
          <p className="text-sm uppercase tracking-wider text-[#888888]">Trash is empty</p>
          <p className="text-xs text-[#444444] mt-1">Deleted items will be staged here for 30 days</p>
        </div>
      ) : (
        <div className="space-y-2">
          {deletedFiles.map((item) => (
            <div
              key={item.id}
              className="p-3 bg-[#0c0c0c] border border-[#282828] hover:border-[#383838] flex items-center justify-between transition-colors text-xs"
            >
              <div className="flex items-center gap-3 truncate max-w-lg">
                {item.thumbnail_url ? (
                  <img
                    src={item.thumbnail_url}
                    alt={item.filename}
                    className="w-10 h-10 object-cover border border-[#222222] flex-shrink-0 grayscale opacity-60"
                  />
                ) : (
                  <div className="w-10 h-10 bg-[#141414] border border-[#222222] flex items-center justify-center text-[#555555] flex-shrink-0">
                    <Trash2 size={16} />
                  </div>
                )}

                <div className="truncate">
                  <div className="font-semibold text-[#cccccc] truncate">{item.filename}</div>
                  <div className="text-[10px] text-[#666666] flex items-center gap-2 mt-0.5">
                    <span>{formatBytes(item.size_bytes)}</span>
                    <span>•</span>
                    <Badge variant="danger">28 Days Left</Badge>
                  </div>
                </div>
              </div>

              <div className="flex items-center gap-2">
                <Button
                  variant="secondary"
                  size="sm"
                  onClick={() => restoreFromTrash([item.id])}
                  icon={<RotateCcw size={12} />}
                >
                  Restore
                </Button>
                <Button
                  variant="danger"
                  size="sm"
                  onClick={() => purgeFiles([item.id])}
                  icon={<Trash2 size={12} />}
                >
                  Purge
                </Button>
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  )
}
