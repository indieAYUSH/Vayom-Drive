import React from 'react'
import { FileItem } from '../../types/vault'
import { Checkbox } from '../common/Checkbox'
import { Badge } from '../common/Badge'
import { Play, FileText, Archive, Film, Star, Music, Trash2, Image } from 'lucide-react'
import { useVaultStore } from '../../store/useVaultStore'
import { formatBytes } from '../../utils/format'

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
        {item.is_interrupted && (
          <Badge variant="danger">
            Interrupted ({item.uploaded_parts || item.parts?.length || 0}/{item.total_parts})
          </Badge>
        )}
        {item.is_split && !item.is_interrupted && (
          <Badge variant="warning">
            {item.total_parts} Parts
          </Badge>
        )}
        {item.is_lossy_video && (
          <Badge variant="info">
            HEVC
          </Badge>
        )}
        {item.is_compressed && !item.is_lossy_video && (
          <Badge variant="default">
            zstd
          </Badge>
        )}
      </div>

      {/* Main Thumbnail / Preview Container */}
      <div 
        onClick={handleCardClick}
        className="w-full aspect-square bg-[#080808] overflow-hidden flex items-center justify-center cursor-pointer relative"
      >
        {item.thumbnail_url || (item.file_category === 'image' && item.preview_url) ? (
          <img
            src={item.thumbnail_url || item.preview_url}
            alt={item.filename}
            loading="lazy"
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

      {/* Technical Metadata Footer */}
      <div className="p-2.5 bg-[#0e0e0e] border-t border-[#1a1a1a] flex flex-col gap-1 font-mono">
        <div className="text-xs text-[#e5e5e5] truncate font-medium" title={item.filename}>
          {item.filename}
        </div>
        <div className="flex items-center justify-between text-[10px] text-[#777777]">
          <span>{formatBytes(item.size_bytes)}</span>
          <span className="uppercase">{item.mime_type.split('/')[1] || 'BIN'}</span>
        </div>
        {item.exif?.camera_model && (
          <div className="text-[9px] text-[#555555] truncate pt-0.5 border-t border-[#161616]">
            {item.exif.camera_make} {item.exif.camera_model}
          </div>
        )}
        {item.is_interrupted && (
          <div className="flex items-center justify-between pt-1 border-t border-[#2a1a1a]">
            <span className="text-[10px] text-[#ff5555]">Interrupted</span>
            <button
              onClick={(e) => {
                e.stopPropagation()
                if (confirm(`Clean up orphan parts for ${item.filename}?`)) {
                  cleanupIncomplete(item.id)
                }
              }}
              className="text-[10px] text-[#ff7777] hover:text-[#ffaaaa] underline flex items-center gap-1"
              title="Purge orphan parts from Telegram WAL"
            >
              <Trash2 size={10} /> Clean up
            </button>
          </div>
        )}
      </div>
    </div>
  )
}
