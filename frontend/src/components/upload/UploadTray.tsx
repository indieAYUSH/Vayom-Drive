import React from 'react'
import { useUploadStore } from '../../store/useUploadStore'
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
    clearCompleted,
    floodWaitSecondsRemaining
  } = useUploadStore()

  if (tasks.length === 0) return null

  const activeTasks = tasks.filter((t) => t.status !== 'done' && t.status !== 'failed')
  const completedTasks = tasks.filter((t) => t.status === 'done')
  const totalSpeed = activeTasks.reduce((acc, t) => acc + (t.speed_bps || 0), 0)

  return (
    <div className="fixed bottom-16 left-2 right-2 sm:left-auto sm:right-4 sm:bottom-4 sm:w-96 z-40 bg-[#111111] border border-[#333333] select-none font-mono text-xs flex flex-col shadow-none">
      {/* Tray Header Bar */}
      <div 
        onClick={() => setTrayOpen(!isTrayOpen)}
        className="px-3 py-2.5 bg-[#0a0a0a] border-b border-[#282828] flex items-center justify-between cursor-pointer text-[#e5e5e5] hover:bg-[#141414] transition-colors"
      >
        <div className="flex items-center gap-2 truncate">
          <UploadCloud size={14} className={activeTasks.length > 0 ? 'text-[#3b82f6] animate-pulse' : 'text-[#888888]'} />
          <span className="font-semibold text-xs truncate">
            {activeTasks.length > 0
              ? `Uploading ${activeTasks.length} ${activeTasks.length === 1 ? 'item' : 'items'}`
              : `Upload complete (${completedTasks.length})`}
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

      {/* Task List (Visible when Open) */}
      {isTrayOpen && (
        <div className="max-h-72 overflow-y-auto p-2 space-y-2 bg-[#0c0c0c]">
          {tasks.map((task) => (
            <TaskRow
              key={task.id}
              task={task}
              onPause={pauseTask}
              onResume={resumeTask}
              onCancel={cancelTask}
            />
          ))}
        </div>
      )}
    </div>
  )
}
