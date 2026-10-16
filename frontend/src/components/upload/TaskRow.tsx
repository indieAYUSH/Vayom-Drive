import React from 'react'
import { UploadTask } from '../../types/upload'
import { Pause, Play, X, Check, AlertCircle } from 'lucide-react'
import { Badge } from '../common/Badge'

interface TaskRowProps {
  task: UploadTask
  onPause: (id: string) => void
  onResume: (id: string) => void
  onCancel: (id: string) => void
}

export const TaskRow: React.FC<TaskRowProps> = ({
  task,
  onPause,
  onResume,
  onCancel
}) => {
  const formatBytes = (bytes: number) => {
    if (bytes >= 1024 * 1024 * 1024) return `${(bytes / (1024 * 1024 * 1024)).toFixed(2)} GB`
    return `${(bytes / (1024 * 1024)).toFixed(1)} MB`
  }

  const formatSpeed = (bps: number) => {
    if (bps <= 0) return ''
    return `${(bps / (1024 * 1024)).toFixed(1)} MB/s`
  }

  const getStatusBadge = () => {
    switch (task.status) {
      case 'queued':
        return <Badge variant="default">Queued</Badge>
      case 'hashing':
        return <Badge variant="info">Hashing SHA256</Badge>
      case 'analyzing':
        return <Badge variant="info">Analyzing</Badge>
      case 'compressing':
        return <Badge variant="warning">Compressing zstd</Badge>
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
      case 'done':
        return <Badge variant="success">Committed</Badge>
      case 'failed':
        return <Badge variant="danger">Failed</Badge>
      case 'paused':
        return <Badge variant="default">Paused</Badge>
    }
  }

  return (
    <div className="p-3 bg-[#141414] border border-[#222222] font-mono text-xs flex flex-col gap-2 select-none">
      {/* Header Info */}
      <div className="flex items-center justify-between">
        <div className="truncate max-w-[200px] font-medium text-[#f0f0f0]" title={task.filename}>
          {task.filename}
        </div>
        <div className="flex items-center gap-1.5">
          {getStatusBadge()}
          {task.status === 'uploading' && (
            <button
              onClick={() => onPause(task.id)}
              className="p-1 text-[#888888] hover:text-[#ffffff] border border-transparent hover:border-[#333333]"
              title="Pause"
            >
              <Pause size={12} />
            </button>
          )}
          {task.status === 'paused' && (
            <button
              onClick={() => onResume(task.id)}
              className="p-1 text-[#888888] hover:text-[#ffffff] border border-transparent hover:border-[#333333]"
              title="Resume"
            >
              <Play size={12} />
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
            task.status === 'done' ? 'bg-[#22c55e]' : 'bg-[#ffffff]'
          }`}
          style={{ width: `${task.progress_percent}%` }}
        />
      </div>

      {/* Speed & Size Stats */}
      <div className="flex items-center justify-between text-[10px] text-[#777777]">
        <span>
          {formatBytes(task.uploaded_bytes)} / {formatBytes(task.size_bytes)} ({task.progress_percent}%)
        </span>
        {task.status === 'uploading' && task.speed_bps > 0 && (
          <span className="text-[#3b82f6]">
            {formatSpeed(task.speed_bps)} • ETA {task.eta_seconds}s
          </span>
        )}
      </div>
    </div>
  )
}
