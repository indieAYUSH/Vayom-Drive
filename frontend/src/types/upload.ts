export type UploadStatus = 
  | 'queued' 
  | 'buffering'
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
  stage?: string
  current_part: number
  total_parts: number
  server_file_id?: string
  error_message?: string
  storage_backend?: 'telegram' | 'nas' | 's3' | 'nextcloud'
  created_at: number
}
