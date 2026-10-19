import React, { useState, useEffect, useRef } from 'react'
import { api } from '../../services/api'
import { useVaultStore } from '../../store/useVaultStore'
import { WatchFolder, WatchFolderEvent } from '../../types/vault'
import {
  FolderSync,
  FolderPlus,
  Play,
  Pause,
  Trash2,
  AlertCircle,
  CheckCircle2,
  Clock,
  HardDrive,
  RefreshCw,
  FolderCheck,
} from 'lucide-react'
import { Button } from '../common/Button'
import { Badge } from '../common/Badge'
import { translateErrorMessage } from '../../utils/errorTranslation'

export const WatchFolderManager: React.FC = () => {
  const { albums } = useVaultStore()
  const [folders, setFolders] = useState<WatchFolder[]>([])
  const [events, setEvents] = useState<WatchFolderEvent[]>([])
  const [isLoading, setIsLoading] = useState(false)
  const [isAdding, setIsAdding] = useState(false)
  const [scanningId, setScanningId] = useState<string | null>(null)
  const [togglingId, setTogglingId] = useState<string | null>(null)
  const isMountedRef = useRef(true)

  // Form State
  const [showAddForm, setShowAddForm] = useState(false)
  const [path, setPath] = useState('')
  const [label, setLabel] = useState('')
  const [targetAlbumId, setTargetAlbumId] = useState('')
  const [autoDelete, setAutoDelete] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [successMsg, setSuccessMsg] = useState<string | null>(null)

  const loadData = async () => {
    try {
      const [fList, eList] = await Promise.all([
        api.listWatchFolders(),
        api.listWatchEvents(undefined, 20),
      ])
      // H-32: don't write state after unmount
      if (!isMountedRef.current) return
      setFolders(fList)
      setEvents(eList)
    } catch (err: any) {
      console.error('Failed to load watch folders:', err)
    }
  }

  useEffect(() => {
    isMountedRef.current = true
    loadData()
    // H-32: refresh folders AND events on the poll so counts don't drift
    const timer = setInterval(loadData, 10000)
    return () => {
      isMountedRef.current = false
      clearInterval(timer)
    }
  }, [])

  const handleCreateFolder = async (e: React.FormEvent) => {
    e.preventDefault()
    if (!path.trim()) return

    setIsAdding(true)
    setError(null)
    setSuccessMsg(null)

    try {
      await api.createWatchFolder({
        path: path.trim(),
        label: label.trim() || undefined,
        target_album_id: targetAlbumId || null,
        auto_delete_local: autoDelete,
      })
      setPath('')
      setLabel('')
      setTargetAlbumId('')
      setAutoDelete(false)
      setShowAddForm(false)
      setSuccessMsg('Watch folder successfully registered and monitoring started.')
      await loadData()
    } catch (err: any) {
      setError(translateErrorMessage(err))
    } finally {
      setIsAdding(false)
    }
  }

  const handleDeleteFolder = async (id: string, folderLabel: string) => {
    if (!window.confirm(`Stop watching and remove "${folderLabel}"?`)) return
    try {
      await api.deleteWatchFolder(id)
      await loadData()
    } catch (err: any) {
      alert(`Unable to delete watch folder: ${translateErrorMessage(err)}`)
    }
  }

  const handleScanFolder = async (id: string) => {
    setScanningId(id)
    try {
      const res = await api.scanWatchFolder(id)
      setSuccessMsg(`Rescan initiated: ${res.queued_files} file(s) queued for ingestion.`)
      await loadData()
    } catch (err: any) {
      alert(`Unable to scan folder: ${translateErrorMessage(err)}`)
    } finally {
      setScanningId(null)
    }
  }

  // H-24: wire the formerly dead updateWatchFolder API into a pause/resume toggle
  const handleToggleFolder = async (folder: WatchFolder) => {
    setTogglingId(folder.id)
    try {
      await api.updateWatchFolder(folder.id, { is_active: !folder.is_active })
      await loadData()
    } catch (err: any) {
      alert(`Unable to update watch folder: ${translateErrorMessage(err)}`)
    } finally {
      setTogglingId(null)
    }
  }

  const formatBytes = (bytes: number) => {
    if (bytes >= 1024 * 1024 * 1024) return `${(bytes / (1024 * 1024 * 1024)).toFixed(2)} GB`
    if (bytes >= 1024 * 1024) return `${(bytes / (1024 * 1024)).toFixed(1)} MB`
    return `${(bytes / 1024).toFixed(1)} KB`
  }

  return (
    <div className="p-4 bg-[#0a0a0a] border border-[#222222] space-y-5 text-xs">
      <div className="flex items-center justify-between pb-3 border-b border-[#1a1a1a]">
        <div>
          <span className="text-xs font-semibold text-[#f0f0f0] tracking-normal flex items-center gap-2">
            <FolderSync size={14} className="text-[#38bdf8]" /> Local Watch Folders
          </span>
          <p className="text-xs text-[#888888] mt-0.5">
            Auto-ingests camera drops, OBS recordings, and local directories with write debounce
          </p>
        </div>
        <div className="flex items-center gap-2">
          <Button
            variant={showAddForm ? "secondary" : "outline"}
            size="sm"
            onClick={() => setShowAddForm(!showAddForm)}
            icon={<FolderPlus size={11} />}
          >
            {showAddForm ? 'Close Form' : 'Register Directory'}
          </Button>
          <Button variant="ghost" size="sm" onClick={loadData} icon={<RefreshCw size={11} />}>
            Refresh
          </Button>
        </div>
      </div>

      {/* Form: Add New Watch Folder (Expands on User Demand) */}
      {showAddForm && (
        <form onSubmit={handleCreateFolder} className="p-3 bg-[#111111] border border-[#222222] space-y-3">
          <div className="text-xs font-medium text-[#cccccc] flex items-center gap-1.5">
            <FolderPlus size={13} /> Register Watch Directory
          </div>

          <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
            <div>
              <label className="text-xs font-medium text-[#cccccc] block mb-1">
                Absolute Local Directory Path *
              </label>
              <input
                type="text"
                placeholder="/home/user/Footage or /mnt/media"
                value={path}
                onChange={(e) => setPath(e.target.value)}
                className="w-full bg-[#050505] border border-[#333333] px-2.5 py-1.5 text-xs text-[#ffffff] focus:outline-none focus:border-[#38bdf8] font-mono"
                required
              />
            </div>

            <div>
              <label className="text-xs font-medium text-[#cccccc] block mb-1">
                Custom Label (Optional)
              </label>
              <input
                type="text"
                placeholder="e.g. Sony A7IV Card Drop"
                value={label}
                onChange={(e) => setLabel(e.target.value)}
                className="w-full bg-[#050505] border border-[#333333] px-2.5 py-1.5 text-xs text-[#ffffff] focus:outline-none focus:border-[#38bdf8]"
              />
            </div>

            <div>
              <label className="text-xs font-medium text-[#cccccc] block mb-1">
                Target Folder (Optional)
              </label>
              <select
                value={targetAlbumId}
                onChange={(e) => setTargetAlbumId(e.target.value)}
                className="w-full bg-[#050505] border border-[#333333] px-2 py-1.5 text-xs text-[#ffffff] focus:outline-none focus:border-[#38bdf8]"
              >
                <option value="">-- No Auto-Folder Assignment --</option>
                {albums.map((a) => (
                  <option key={a.id} value={a.id}>
                    {a.name}
                  </option>
                ))}
              </select>
            </div>

            <div className="flex items-center pt-5">
              <label className="flex items-center gap-2 cursor-pointer text-[#cccccc] hover:text-[#ffffff]">
                <input
                  type="checkbox"
                  checked={autoDelete}
                  onChange={(e) => setAutoDelete(e.target.checked)}
                  className="w-3.5 h-3.5 accent-[#38bdf8] bg-[#050505] border border-[#333333]"
                />
                <span className="text-xs">Auto-delete local file after confirmed sync</span>
              </label>
            </div>
          </div>

          {error && (
            <div className="text-[11px] text-[#ff4444] flex items-center gap-1.5 pt-1">
              <AlertCircle size={12} /> {error}
            </div>
          )}

          {successMsg && (
            <div className="text-[11px] text-[#22c55e] flex items-center gap-1.5 pt-1">
              <CheckCircle2 size={12} /> {successMsg}
            </div>
          )}

          <div className="pt-1 flex items-center justify-end gap-2">
            <Button
              type="button"
              variant="ghost"
              size="sm"
              onClick={() => setShowAddForm(false)}
            >
              Cancel
            </Button>
            <Button
              type="submit"
              variant="primary"
              size="sm"
              disabled={isAdding || !path.trim()}
              icon={<FolderCheck size={12} />}
            >
              {isAdding ? 'Validating...' : 'Start Watching Directory'}
            </Button>
          </div>
        </form>
      )}

      {/* Watch Folders List */}
      <div className="space-y-2">
        <div className="text-xs font-semibold text-[#888888]">
          Configured Directories ({folders.length})
        </div>

        {folders.length === 0 ? (
          <div className="p-4 border border-[#1a1a1a] bg-[#050505] text-center text-[#888888] text-xs">
            No local directories configured for watching. Add one above to auto-upload footage as it arrives.
          </div>
        ) : (
          <div className="space-y-2">
            {folders.map((f) => (
              <div
                key={f.id}
                className="p-3 bg-[#0d0d0d] border border-[#222222] hover:border-[#333333] transition-colors flex flex-col sm:flex-row sm:items-center justify-between gap-3"
              >
                <div className="space-y-1 truncate">
                  <div className="flex items-center gap-2">
                    <span className="font-medium text-xs text-[#f0f0f0]">{f.label}</span>
                    <Badge variant={f.exists ? (f.is_active ? 'success' : 'default') : 'danger'}>
                      {!f.exists ? 'Directory Missing' : f.is_active ? 'Active Watcher' : 'Paused'}
                    </Badge>
                    {f.auto_delete_local && (
                      <span className="text-[10px] font-mono text-[#ff8800] border border-[#ff8800]/40 px-1 py-0.5">
                        AUTO-PURGE
                      </span>
                    )}
                  </div>
                  <div className="text-xs text-[#888888] font-mono truncate" title={f.path}>
                    {f.path}
                  </div>
                  <div className="text-xs text-[#888888] flex items-center gap-3">
                    <span>Synced: <strong className="text-[#22c55e] font-mono text-[11px]">{f.synced_count || 0}</strong></span>
                    {Boolean(f.syncing_count) && (
                      <span>Syncing: <strong className="text-[#38bdf8] font-mono text-[11px]">{f.syncing_count}</strong></span>
                    )}
                    {Boolean(f.failed_count) && (
                      <span>Failed: <strong className="text-[#ff4444] font-mono text-[11px]">{f.failed_count}</strong></span>
                    )}
                    {f.last_synced_at && (
                      <span className="text-[#888888] flex items-center gap-1 font-mono text-[11px]">
                        <Clock size={11} /> {new Date(f.last_synced_at).toLocaleTimeString()}
                      </span>
                    )}
                  </div>
                </div>

                <div className="flex items-center gap-2 flex-shrink-0">
                  <Button
                    variant="secondary"
                    size="sm"
                    onClick={() => handleToggleFolder(f)}
                    disabled={togglingId === f.id || !f.exists}
                    icon={f.is_active ? <Pause size={11} /> : <Play size={11} />}
                  >
                    {f.is_active ? 'Pause' : 'Resume'}
                  </Button>
                  <Button
                    variant="secondary"
                    size="sm"
                    onClick={() => handleScanFolder(f.id)}
                    disabled={scanningId === f.id || !f.exists}
                    icon={<Play size={11} className={scanningId === f.id ? 'animate-spin' : ''} />}
                  >
                    {scanningId === f.id ? 'Scanning...' : 'Scan Now'}
                  </Button>
                  <Button
                    variant="danger"
                    size="sm"
                    onClick={() => handleDeleteFolder(f.id, f.label)}
                    icon={<Trash2 size={11} />}
                  >
                    Remove
                  </Button>
                </div>
              </div>
            ))}
          </div>
        )}
      </div>

      {/* Ingestion Event Stream Drawer */}
      {events.length > 0 && (
        <div className="space-y-2 pt-2 border-t border-[#1a1a1a]">
          <div className="text-[10px] text-[#666666] uppercase tracking-wider flex items-center gap-1.5">
            <HardDrive size={11} /> Recent Ingestion Events
          </div>
          <div className="p-2 bg-[#050505] border border-[#1a1a1a] max-h-40 overflow-y-auto space-y-1 text-[10px]">
            {events.map((ev) => (
              <div
                key={ev.id}
                className="flex items-center justify-between text-[#888888] font-mono border-b border-[#111111] pb-1 last:border-b-0"
              >
                <div className="truncate max-w-[70%] flex items-center gap-1.5">
                  <span
                    className={
                      ev.status === 'synced'
                        ? 'text-[#22c55e]'
                        : ev.status === 'syncing'
                        ? 'text-[#00e5ff] animate-pulse'
                        : 'text-[#ff4444]'
                    }
                  >
                    ●
                  </span>
                  <span className="text-[#cccccc] truncate">{ev.file_path.split('/').pop()}</span>
                  <span className="text-[#555555]">({formatBytes(ev.file_size_bytes)})</span>
                </div>
                <div className="flex items-center gap-2 text-[9px] text-[#555555] flex-shrink-0">
                  <span className="uppercase">{ev.status}</span>
                  <span>{new Date(ev.created_at).toLocaleTimeString()}</span>
                </div>
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  )
}
