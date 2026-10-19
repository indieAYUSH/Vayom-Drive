import React from 'react'
import { UploadTask } from '../../types/upload'
import { Pause, Play, X, Check, AlertCircle, RotateCcw } from 'lucide-react'
import { Badge } from '../common/Badge'
import { formatBytes, formatSpeed } from '../../utils/format'

interface TaskRowProps {
  task: UploadTask
  onPause: (id: string) => void
  onResume: (id: string) => void
  onRetry: (id: string) => void
  onCancel: (id: string) => void
}

export const TaskRow: React.FC<TaskRowProps> = ({
  task,
  onPause,
  onResume,
  onRetry,
  onCancel
}) => {

  const getStatusBadge = () => {
    switch (task.status) {
      case 'queued':
        return <Badge variant="default">Queued</Badge>
      case 'buffering':
        return <Badge variant="info">Buffering to Node</Badge>
      case 'hashing':
        return <Badge variant="info">Hashing SHA256</Badge>
      case 'analyzing':
        return <Badge variant="info">Analyzing</Badge>
      case 'compressing':
        return <Badge variant="warning">Compressing</Badge>
      case 'splitting':
        return <Badge variant="warning">Splitting {task.total_parts} Parts</Badge>
      case 'uploading':
        return (
          <Badge variant="info">
            {task.total_parts > 1 ? `Uploading Part ${task.current_part}/${task.total_parts}` : 'Uploading to TG'}
          </Badge>
        )
      case 'verifying':
        return <Badge variant="warning">Verifying</Badge>
      case 'rate_limited':
        return <Badge variant="warning">Rate Limited (Cooldown)</Badge>
      case 'done':
        return <Badge variant="success">Committed</Badge>
      case 'failed':
        return <Badge variant="danger">Failed</Badge>
      case 'paused':
        return <Badge variant="default">Paused</Badge>
    }
  }

  return (
    <div className="p-3 bg-[#141414] border border-[#222222] text-xs flex flex-col gap-2 select-none">
      {/* Header Info */}
      <div className="flex items-center justify-between">
        <div className="truncate max-w-[200px] font-medium text-xs text-[#f0f0f0]" title={task.filename}>
          {task.filename}
        </div>
        <div className="flex items-center gap-1.5">
          {getStatusBadge()}
          {(task.status === 'uploading' || task.status === 'buffering') && (
            <button
              onClick={() => onPause(task.id)}
              className="p-1 text-[#888888] hover:text-[#ffffff] border border-transparent hover:border-[#333333]"
              title="Pause"
            >
              <Pause size={12} />
            </button>
          )}
          {(task.status === 'paused' || task.status === 'rate_limited') && (
            <button
              onClick={() => onResume(task.id)}
              className="p-1 text-[#888888] hover:text-[#ffffff] border border-transparent hover:border-[#333333]"
              title="Resume"
            >
              <Play size={12} />
            </button>
          )}
          {/* H-31: failed tasks can be retried instead of only cancelled */}
          {task.status === 'failed' && (
            <button
              onClick={() => onRetry(task.id)}
              className="p-1 text-[#888888] hover:text-[#22c55e] border border-transparent hover:border-[#144422]"
              title="Retry"
            >
              <RotateCcw size={12} />
            </button>
          )}
          {task.status !== 'done' && (
            <button
              onClick={() => onCancel(task.id)}
              className="p-1 text-[#888888] hover:text-[#ef4444] border border-transparent hover:border-[#441111]"
              title="Cancel"
            >
              <X size={12} />
            </button>
          )}
        </div>
      </div>

      {/* Progress Bar */}
      <div className="w-full h-1.5 bg-[#202020] overflow-hidden">
        <div
          className={`h-full transition-all duration-200 ${
            task.status === 'done'
              ? 'bg-[#22c55e]'
              : task.status === 'hashing' || task.status === 'analyzing' || task.status === 'splitting' || task.status === 'verifying'
              ? 'bg-[#3b82f6] animate-pulse w-full'
              : 'bg-[#ffffff]'
          }`}
          style={
            task.status === 'hashing' || task.status === 'analyzing' || task.status === 'splitting' || task.status === 'verifying'
              ? undefined
              : { width: `${Math.min(100, Math.max(0, task.progress_percent))}%` }
          }
        />
      </div>

      {/* Speed, Stage & Size Stats */}
      <div className="flex items-center justify-between text-xs text-[#888888] font-mono">
        <div className="flex items-center gap-1.5 truncate">
          {task.status === 'buffering' && (
            <span>
              Buffering: {formatBytes(task.uploaded_bytes)} / {formatBytes(task.size_bytes)} ({task.progress_percent}%)
            </span>
          )}

          {(task.status === 'hashing' || task.status === 'analyzing' || task.status === 'splitting') && (
            <span>
              Processing: {task.stage || 'Optimizing media on node...'}
            </span>
          )}

          {task.status === 'uploading' && (
            <>
              <span>
                Upload: {formatBytes(task.uploaded_bytes)} / {formatBytes(task.size_bytes)} ({task.progress_percent}%)
              </span>
              {task.stage && (
                <span className="text-[#888888] truncate hidden sm:inline">• {task.stage}</span>
              )}
            </>
          )}

          {task.status === 'verifying' && (
            <span>
              Syncing encrypted index...
            </span>
          )}

          {task.status === 'done' && (
            <span>
              {formatBytes(task.size_bytes)} • Completed
            </span>
          )}

          {(task.status === 'queued' || task.status === 'paused' || task.status === 'failed' || task.status === 'rate_limited') && (
            <span>
              {formatBytes(task.uploaded_bytes)} / {formatBytes(task.size_bytes)}
              {task.stage ? ` • ${task.stage}` : ''}
            </span>
          )}
        </div>

        <div className="flex items-center gap-1.5 shrink-0 font-mono">
          {task.status === 'buffering' && task.speed_bps > 0 && (
            <span className="text-[#38bdf8]">
              {formatSpeed(task.speed_bps)} • Gateway
            </span>
          )}

          {task.status === 'uploading' && task.speed_bps > 0 && (
            <span className="text-[#38bdf8]">
              {formatSpeed(task.speed_bps)} • ETA {task.eta_seconds}s
            </span>
          )}

          {task.status === 'hashing' && (
            <span className="text-[#3b82f6] animate-pulse">Hashing SHA-256</span>
          )}

          {task.status === 'analyzing' && (
            <span className="text-[#3b82f6] animate-pulse">Extracting Metadata</span>
          )}

          {task.status === 'splitting' && (
            <span className="text-[#f59e0b] animate-pulse">Splitting Parts</span>
          )}

          {task.status === 'verifying' && (
            <span className="text-[#f59e0b] animate-pulse">Syncing Catalog</span>
          )}

          {task.status === 'rate_limited' && (
            <span className="text-[#f59e0b] animate-pulse">Safeguarding Account</span>
          )}

          {task.status === 'done' && (
            <span className="text-[#22c55e]">Completed</span>
          )}
        </div>
      </div>

      {/* Error or Cooldown Notice */}
      {task.error_message && (
        <div className="flex items-center gap-1.5 text-[10px] text-[#f59e0b] bg-[#221605] p-1.5 border border-[#78350f]">
          <AlertCircle size={11} className="shrink-0 text-[#f59e0b]" />
          <span className="truncate">{task.error_message}</span>
        </div>
      )}
    </div>
  )
}
