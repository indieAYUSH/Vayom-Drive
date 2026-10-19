import React, { useState } from 'react'
import { FileItem } from '../../types/vault'
import { Checkbox } from '../common/Checkbox'
import { Badge } from '../common/Badge'
import { Play, FileText, Archive, Film, Star, Music, Trash2, Image } from 'lucide-react'
import { useVaultStore } from '../../store/useVaultStore'
import { formatBytes } from '../../utils/format'
import { resolveMediaUrl } from '../../utils/apiUrl'

interface MediaCardProps {
  item: FileItem
  isSelected: boolean
  onToggleSelect: (id: string) => void
  onOpenLightbox: (item: FileItem) => void
}

export const MediaCard: React.FC<MediaCardProps> = ({
  item,
  isSelected,
  onToggleSelect,
  onOpenLightbox,
}) => {
  const { starredIds, toggleStar, setActiveAudioItem, cleanupIncomplete } = useVaultStore()
  const isStarred = starredIds.includes(item.id)
  const [thumbError, setThumbError] = useState(false)

  const isAudio =
    item.file_category === 'audio' ||
    item.mime_type.startsWith('audio/') ||
    /\.(mp3|wav|flac|ogg|m4a|aac)$/i.test(item.filename)

  const formatDuration = (secs?: number) => {
    if (!secs) return null
    const m = Math.floor(secs / 60)
    const s = Math.floor(secs % 60)
    return `${m.toString().padStart(2, '0')}:${s.toString().padStart(2, '0')}`
  }

  const handleCardClick = () => {
    if (isAudio) {
      setActiveAudioItem(item)
    } else {
      onOpenLightbox(item)
    }
  }

  return (
    <div 
      className={`group relative bg-[#0c0c0c] border transition-colors select-none ${
        isSelected ? 'border-[#ffffff]' : 'border-[#1c1c1c] hover:border-[#383838]'
      }`}
    >
      {/* Top Checkbox Overlay */}
      <div className="absolute top-2 left-2 z-10 opacity-0 group-hover:opacity-100 transition-opacity bg-[#080808] p-1 border border-[#282828]">
        <Checkbox
          checked={isSelected}
          onChange={() => onToggleSelect(item.id)}
        />
      </div>

      {/* Persistent Selection Indicator if checked */}
      {isSelected && (
        <div className="absolute top-2 left-2 z-10 bg-[#080808] p-1 border border-[#ffffff]">
          <Checkbox
            checked={true}
            onChange={() => onToggleSelect(item.id)}
          />
        </div>
      )}

      {/* Top Right: Badges & Star Button */}
      <div className="absolute top-2 right-2 z-10 flex flex-col items-end gap-1">
        <button
          onClick={(e) => {
            e.stopPropagation()
            toggleStar(item.id)
          }}
          className={`p-1 border border-[#282828] bg-[#080808] transition-opacity ${
            isStarred ? 'text-[#ffffff] opacity-100' : 'text-[#666666] opacity-0 group-hover:opacity-100 hover:text-[#ffffff]'
          }`}
          title={isStarred ? 'Remove from Starred' : 'Star file'}
        >
          <Star size={12} fill={isStarred ? 'white' : 'none'} />
        </button>

        {/* Storage Backend Mini Badge */}
        {(() => {
          const backend = item.storage_backend || 'telegram'
          if (backend === 'nas') {
            return (
              <span className="px-1.5 py-0.5 text-[9px] font-mono font-bold tracking-wider bg-emerald-950/80 text-emerald-400 border border-emerald-500/40" title="Stored on Local / NAS Storage">
                NAS
              </span>
            )
          } else if (backend === 's3') {
            return (
              <span className="px-1.5 py-0.5 text-[9px] font-mono font-bold tracking-wider bg-amber-950/80 text-amber-400 border border-amber-500/40" title="Stored on S3 / R2 / MinIO Storage">
                S3
              </span>
            )
          } else if (backend === 'nextcloud') {
            return (
              <span className="px-1.5 py-0.5 text-[9px] font-mono font-bold tracking-wider bg-purple-950/80 text-purple-400 border border-purple-500/40" title="Stored on Nextcloud WebDAV Storage">
                NC
              </span>
            )
          } else {
            return (
              <span className="px-1.5 py-0.5 text-[9px] font-mono font-bold tracking-wider bg-sky-950/80 text-sky-400 border border-sky-500/40" title="Stored on Telegram Cloud Storage">
                TG
              </span>
            )
          }
        })()}

        {item.is_interrupted && (
          <Badge variant="danger">
            Interrupted ({item.uploaded_parts || item.parts?.length || 0}/{item.total_parts})
          </Badge>
        )}
      </div>

      {/* Main Thumbnail / Preview Container */}
      <div 
        onClick={handleCardClick}
        className="w-full aspect-square bg-[#080808] overflow-hidden flex items-center justify-center cursor-pointer relative"
      >
        {!thumbError && item.thumbnail_url ? (
          <img
            src={resolveMediaUrl(item.thumbnail_url)}
            alt={item.filename}
            loading="lazy"
            onError={() => setThumbError(true)}
            draggable={false}
            onDragStart={(e) => e.preventDefault()}
            className="w-full h-full object-cover transition-transform duration-200 group-hover:scale-[1.02] select-none"
          />
        ) : item.file_category === 'image' ? (
          <div className="flex flex-col items-center justify-center text-[#555555] gap-2">
            <Image size={36} strokeWidth={1.5} />
            <span className="text-[10px] font-mono text-[#888888]">IMAGE</span>
          </div>
        ) : item.file_category === 'video' ? (
          <div className="flex flex-col items-center justify-center text-[#555555] gap-2">
            <Film size={36} strokeWidth={1.5} />
            <span className="text-[10px] font-mono text-[#888888]">VIDEO</span>
          </div>
        ) : item.file_category === 'archive' ? (
          <div className="flex flex-col items-center justify-center text-[#555555] gap-2">
            <Archive size={36} strokeWidth={1.5} />
            <span className="text-[10px] font-mono text-[#888888]">ARCHIVE</span>
          </div>
        ) : isAudio ? (
          <div className="flex flex-col items-center justify-center text-[#555555] gap-2">
            <Music size={36} strokeWidth={1.5} />
            <span className="text-[10px] font-mono text-[#888888]">AUDIO</span>
          </div>
        ) : (
          <div className="flex flex-col items-center justify-center text-[#555555] gap-2">
            <FileText size={36} strokeWidth={1.5} />
            <span className="text-[10px] font-mono text-[#888888]">DOCUMENT</span>
          </div>
        )}

        {/* Video Play Overlay & Duration */}
        {item.file_category === 'video' && (
          <div className="absolute inset-0 flex items-center justify-center bg-black bg-opacity-30 group-hover:bg-opacity-10 transition-colors">
            <div className="w-10 h-10 bg-[#000000] border border-[#ffffff] flex items-center justify-center text-[#ffffff]">
              <Play size={16} fill="white" />
            </div>
            {item.duration_seconds && (
              <div className="absolute bottom-2 right-2 px-1.5 py-0.5 bg-[#000000] border border-[#282828] text-[10px] font-mono text-[#ffffff]">
                {formatDuration(item.duration_seconds)}
              </div>
            )}
          </div>
        )}
      </div>

      {/* Metadata Footer */}
      <div 
        onClick={handleCardClick}
        className="p-2.5 bg-[#0e0e0e] border-t border-[#1a1a1a] flex flex-col gap-1 font-sans cursor-pointer hover:bg-[#121212] transition-colors"
      >
        <div className="text-xs text-[#f0f0f0] truncate font-medium" title={item.filename}>
          {item.filename}
        </div>
        <div className="flex items-center justify-between text-[11px] text-[#888888]">
          <span className="font-mono text-[#a3a3a3]">{formatBytes(item.size_bytes)}</span>
          <div className="flex items-center gap-1.5 uppercase font-mono text-[10px]">
            {item.is_split && (
              <span className="text-[9px] text-[#eab308] border border-[#eab308]/40 px-1 py-0.2" title={`${item.total_parts} Split Parts`}>
                {item.total_parts}P
              </span>
            )}
            {item.is_lossy_video && (
              <span className="text-[9px] text-[#38bdf8] border border-[#38bdf8]/40 px-1 py-0.2" title="HEVC Encoded">
                HEVC
              </span>
            )}
            <span className="text-[9px] text-[#888888] font-bold">
              {item.storage_backend === 'nas' ? 'NAS' : item.storage_backend === 's3' ? 'S3' : item.storage_backend === 'nextcloud' ? 'NC' : 'TG'}
            </span>
            <span>•</span>
            <span className="text-[#a3a3a3]">{item.mime_type.split('/')[1]?.toUpperCase() || 'FILE'}</span>
          </div>
        </div>
        {item.exif?.camera_model && (
          <div className="text-[11px] font-sans text-[#888888] truncate pt-0.5 border-t border-[#161616]" title={`${item.exif.camera_make || ''} ${item.exif.camera_model || ''}`}>
            {item.exif.camera_make} {item.exif.camera_model}
          </div>
        )}
        {item.is_interrupted && (
          <div className="flex items-center justify-between pt-1 border-t border-[#2a1a1a]">
            <span className="text-[11px] font-sans text-[#ff5555]">Interrupted</span>
            <button
              onClick={(e) => {
                e.stopPropagation()
                if (confirm(`Remove incomplete parts for ${item.filename}?`)) {
                  cleanupIncomplete(item.id).catch((err: any) => alert(err?.message || 'Failed to remove incomplete parts'))
                }
              }}
              className="text-[11px] font-sans text-[#ff7777] hover:text-[#ffaaaa] underline flex items-center gap-1"
              title="Remove incomplete upload parts"
            >
              <Trash2 size={10} /> Clean up
            </button>
          </div>
        )}
      </div>
    </div>
  )
}
