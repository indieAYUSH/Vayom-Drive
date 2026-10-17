export type FileCategory = 'image' | 'video' | 'screenshot' | 'document' | 'archive' | 'audio' | 'other'

export interface TelegramPartManifest {
  part_index: number
  offset: number
  size: number
  telegram_message_id: number
  sha256: string
}

export interface ExifMetadata {
  camera_make?: string
  camera_model?: string
  lens?: string
  focal_length?: string
  aperture?: string
  iso?: number
  shutter_speed?: string
  dimensions?: {
    width: number
    height: number
  }
}

export interface GpsCoordinates {
  latitude: number
  longitude: number
  altitude?: number
}

export interface FileItem {
  id: string
  filename: string
  mime_type: string
  file_category: FileCategory
  size_bytes: number
  sha256: string
  is_compressed: boolean
  compression_ratio?: number
  is_lossy_video: boolean
  is_split: boolean
  total_parts: number
  parts: TelegramPartManifest[]
  thumbnail_url?: string
  preview_url?: string
  blur_hash?: string
  exif?: ExifMetadata
  gps?: GpsCoordinates
  duration_seconds?: number
  tags: string[]
  albums: string[]
  folders?: string[]
  is_screenshot?: boolean
  is_starred?: boolean
  relative_path?: string
  taken_at?: string
  created_at: string
  is_deleted: boolean
  deleted_at?: string
}

export interface AlbumItem {
  id: string
  name: string
  description?: string
  cover_url?: string
  file_count: number
  created_at: string
}

export type FolderItem = AlbumItem

export interface ShareLinkItem {
  id: string
  token: string
  file_id: string
  filename: string
  file_size: number
  expires_at: string
  max_views?: number
  view_count: number
  has_password: boolean
  is_revoked: boolean
  created_at: string
}

export interface StorageMetrics {
  total_bytes: number
  total_files: number
  channel_id: string
  telegram_messages: number
  online_status: 'online' | 'flood_wait' | 'reconnecting'
  flood_wait_seconds?: number
}
