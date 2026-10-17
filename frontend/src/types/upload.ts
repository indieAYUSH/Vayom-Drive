export type UploadStatus = 
  | 'queued' 
  | 'hashing' 
  | 'analyzing' 
  | 'compressing' 
  | 'splitting' 
  | 'uploading' 
  | 'verifying' 
  | 'rate_limited'
  | 'done' 
  | 'failed' 
  | 'paused'

export interface UploadTask {
  id: string
  file: File
  filename: string
  size_bytes: number
  uploaded_bytes: number
  progress_percent: number
  speed_bps: number
  eta_seconds: number
  status: UploadStatus
  current_part: number
  total_parts: number
  error_message?: string
  created_at: number
}
