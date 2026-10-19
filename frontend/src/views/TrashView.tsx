import React from 'react'
import { useVaultStore } from '../store/useVaultStore'
import { Trash2, RotateCcw } from 'lucide-react'
import { Button } from '../components/common/Button'
import { Badge } from '../components/common/Badge'
import { formatBytes } from '../utils/format'
import { resolveMediaUrl } from '../utils/apiUrl'

// H-22: compute remaining retention days from the actual deletion date (30-day policy)
const formatDaysLeft = (deletedAt?: string) => {
  const ts = deletedAt ? new Date(deletedAt).getTime() : NaN
  if (Number.isNaN(ts)) return '30 Days Left'
  const days = Math.max(0, 30 - Math.floor((Date.now() - ts) / 86400000))
  return `${days} ${days === 1 ? 'Day' : 'Days'} Left`
}

export const TrashView: React.FC = () => {
  const { files, restoreFromTrash, purgeFiles, emptyTrash } = useVaultStore()

  const deletedFiles = files.filter((f) => f.is_deleted)

  const handleRestoreAll = () => {
    // H-17: surface failures instead of an unhandled rejection
    restoreFromTrash(deletedFiles.map((f) => f.id)).catch((err: any) => alert(err?.message || 'Failed to restore items'))
  }

  const handleEmptyTrash = async () => {
    if (window.confirm('Permanently purge all items from trash and Telegram? This cannot be undone.')) {
      await emptyTrash()
    }
  }

  return (
    <div className="flex-1 overflow-y-auto p-6 bg-[#000000] select-none">
      {/* Top Header */}
      <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-4 pb-6 border-b border-[#282828] mb-6">
        <div>
          <h1 className="text-sm font-semibold text-[#ef4444] flex items-center gap-2">
            <Trash2 size={16} /> Trash & 30-Day Retention
          </h1>
          <p className="text-xs text-[#888888] mt-1">
            Items remain in trash for 30 days before automated permanent purge.
          </p>
        </div>

        {deletedFiles.length > 0 && (
          <div className="flex items-center gap-2 flex-wrap">
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
              onClick={() => { handleEmptyTrash().catch((err: any) => alert(err?.message || 'Failed to empty trash')) }}
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
          <p className="text-sm font-medium text-[#cccccc]">Trash is empty</p>
          <p className="text-xs text-[#888888] mt-1">Deleted items will be staged here for 30 days</p>
        </div>
      ) : (
        <div className="space-y-2">
          {deletedFiles.map((item) => (
            <div
              key={item.id}
              className="p-3 bg-[#0c0c0c] border border-[#282828] hover:border-[#383838] flex flex-col sm:flex-row sm:items-center justify-between gap-2.5 transition-colors text-xs"
            >
              <div className="flex items-center gap-3 truncate min-w-0 flex-1">
                {item.thumbnail_url ? (
                  <img
                    src={resolveMediaUrl(item.thumbnail_url)}
                    alt={item.filename}
                    draggable={false}
                    onDragStart={(e) => e.preventDefault()}
                    className="w-10 h-10 object-cover border border-[#222222] flex-shrink-0 grayscale opacity-60 select-none"
                  />
                ) : (
                  <div className="w-10 h-10 bg-[#141414] border border-[#222222] flex items-center justify-center text-[#555555] flex-shrink-0">
                    <Trash2 size={16} />
                  </div>
                )}

                <div className="truncate min-w-0 flex-1">
                  <div className="font-medium text-xs text-[#f0f0f0] truncate">{item.filename}</div>
                  <div className="text-xs text-[#888888] flex items-center gap-2 mt-0.5">
                    <span className="font-mono text-[11px] text-[#888888]">{formatBytes(item.size_bytes)}</span>
                    <span>•</span>
                    <Badge variant="danger" className="font-mono text-[10px]">{formatDaysLeft(item.deleted_at)}</Badge>
                  </div>
                </div>
              </div>

              <div className="flex items-center gap-2 self-end sm:self-center flex-shrink-0">
                <Button
                  variant="secondary"
                  size="sm"
                  onClick={() => restoreFromTrash([item.id]).catch((err: any) => alert(err?.message || 'Failed to restore item'))}
                  icon={<RotateCcw size={12} />}
                >
                  Restore
                </Button>
                <Button
                  variant="danger"
                  size="sm"
                  onClick={() => {
                    // H-22: permanent purge requires an explicit confirmation
                    if (window.confirm(`Permanently purge "${item.filename}"? This cannot be undone.`)) {
                      purgeFiles([item.id]).catch((err: any) => alert(err?.message || 'Failed to purge item'))
                    }
                  }}
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
