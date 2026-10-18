import React from 'react'
import { useUploadStore } from '../../store/useUploadStore'
import { useVaultStore } from '../../store/useVaultStore'
import { TaskRow } from './TaskRow'
import { ChevronUp, ChevronDown, UploadCloud, CheckCircle2, Trash, ShieldAlert } from 'lucide-react'
import { formatSpeed } from '../../utils/format'

export const UploadTray: React.FC = () => {
  const { 
    tasks, 
    isTrayOpen, 
    setTrayOpen, 
    pauseTask, 
    resumeTask, 
    cancelTask, 
    retryTask,
    clearCompleted,
    clearFailed,
    floodWaitSecondsRemaining,
    selectedBackend,
    setSelectedBackend
  } = useUploadStore()
  const { license, setActiveTab } = useVaultStore()

  if (tasks.length === 0) return null

  const activeTasks = tasks.filter((t) => t.status !== 'done' && t.status !== 'failed')
  const completedTasks = tasks.filter((t) => t.status === 'done')
  const failedTasks = tasks.filter((t) => t.status === 'failed')
  const uploadingTasks = tasks.filter((t) =>
    ['buffering', 'hashing', 'analyzing', 'compressing', 'splitting', 'uploading', 'verifying'].includes(t.status)
  )
  const rateLimitedTasks = tasks.filter((t) => t.status === 'rate_limited')
  const pausedTasks = tasks.filter((t) => t.status === 'paused')
  const queuedTasks = tasks.filter((t) => t.status === 'queued')
  const totalSpeed = activeTasks.reduce((acc, t) => acc + (t.speed_bps || 0), 0)

  // H-35: report the actual state instead of calling paused/limit-waiting tasks "Uploading"
  const headerLabel =
    uploadingTasks.length > 0
      ? `Uploading ${uploadingTasks.length} ${uploadingTasks.length === 1 ? 'item' : 'items'}`
      : rateLimitedTasks.length > 0
      ? `${rateLimitedTasks.length} rate-limited`
      : pausedTasks.length > 0
      ? `${pausedTasks.length} paused`
      : queuedTasks.length > 0
      ? `${queuedTasks.length} waiting`
      : failedTasks.length > 0
      ? `${failedTasks.length} failed`
      : `Upload complete (${completedTasks.length})`

  return (
    <div className="fixed bottom-16 left-2 right-2 sm:left-auto sm:right-4 sm:bottom-4 sm:w-96 z-40 bg-[#111111] border border-[#333333] select-none font-mono text-xs flex flex-col shadow-none">
      {/* Tray Header Bar */}
      <div 
        onClick={() => setTrayOpen(!isTrayOpen)}
        className="px-3 py-2.5 bg-[#0a0a0a] border-b border-[#282828] flex items-center justify-between cursor-pointer text-[#e5e5e5] hover:bg-[#141414] transition-colors"
      >
        <div className="flex items-center gap-2 truncate">
          <UploadCloud size={14} className={uploadingTasks.length > 0 ? 'text-[#3b82f6] animate-pulse' : 'text-[#888888]'} />
          <span className="font-semibold text-xs truncate">
            {headerLabel}
          </span>
          {totalSpeed > 0 && (
            <span className="text-[10px] text-[#3b82f6] hidden sm:inline">
              ({formatSpeed(totalSpeed)})
            </span>
          )}
        </div>

        <div className="flex items-center gap-1.5">
          {completedTasks.length > 0 && (
            <button
              onClick={(e) => {
                e.stopPropagation()
                clearCompleted()
              }}
              className="text-[10px] text-[#888888] hover:text-[#f0f0f0] px-1.5 py-0.5 border border-[#282828] hover:border-[#444444]"
              title="Clear completed"
            >
              Clear
            </button>
          )}
          {failedTasks.length > 0 && (
            <button
              onClick={(e) => {
                e.stopPropagation()
                clearFailed()
              }}
              className="text-[10px] text-[#888888] hover:text-[#ef4444] px-1.5 py-0.5 border border-[#282828] hover:border-[#441111]"
              title="Clear failed"
            >
              Clear failed
            </button>
          )}
          <button className="text-[#888888]">
            {isTrayOpen ? <ChevronDown size={14} /> : <ChevronUp size={14} />}
          </button>
        </div>
      </div>

      {/* Anti-Ban Rate Limit Shield Alert */}
      {floodWaitSecondsRemaining > 0 && (
        <div className="px-3 py-2 bg-[#2a1b05] border-b border-[#78350f] text-[#f59e0b] flex items-center gap-2 text-[11px]">
          <ShieldAlert size={14} className="shrink-0 text-[#f59e0b] animate-pulse" />
          <span className="leading-tight">
            🛡️ Anti-Ban Cooldown Active ({floodWaitSecondsRemaining}s). Uploads paused to safeguard account.
          </span>
        </div>
      )}

      {/* Destination Selector Bar & Task List */}
      {isTrayOpen && (
        <>
          <div className="px-3 py-1.5 bg-[#0a0a0a] border-b border-[#222222] flex items-center justify-between text-[11px]">
            <span className="text-[#666666]">DESTINATION:</span>
            <div className="flex items-center gap-1 font-bold">
              {(['telegram', 'nas', 's3', 'nextcloud'] as const).map((b) => {
                const isSelected = selectedBackend === b
                const isLocked = b !== 'telegram' && !license?.is_pro
                return (
                  <button
                    key={b}
                    onClick={() => {
                      if (isLocked) {
                        setActiveTab('settings')
                      } else {
                        setSelectedBackend(b)
                      }
                    }}
                    className={`px-1.5 py-0.5 text-[10px] tracking-wider border ${
                      isSelected
                        ? 'bg-[#ffffff] text-[#000000] border-[#ffffff]'
                        : isLocked
                        ? 'bg-[#141414] text-[#555555] border-[#252525]'
                        : 'bg-[#141414] text-[#888888] border-[#252525] hover:text-[#ffffff]'
                    }`}
                    title={isLocked ? `${b.toUpperCase()} requires Pro license` : `Destination: ${b.toUpperCase()}`}
                  >
                    <span>{b === 'telegram' ? 'TG' : b.toUpperCase()}</span>
                    {isLocked && <span className="ml-0.5 text-[8px]">🔒</span>}
                  </button>
                )
              })}
            </div>
          </div>

          <div className="max-h-72 overflow-y-auto p-2 space-y-2 bg-[#0c0c0c]">
            {tasks.map((task) => (
            <TaskRow
              key={task.id}
              task={task}
              onPause={pauseTask}
              onResume={resumeTask}
              onRetry={retryTask}
              onCancel={cancelTask}
            />
          ))}
        </div>
      </>
    )}
    </div>
  )
}
